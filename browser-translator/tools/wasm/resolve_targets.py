#!/usr/bin/env python3
"""Dynamic Wasm Hook target resolution for the Monmusu TD Chinese patch.

Reads an OFFICIAL (unmodified) ntd.wasm plus the IL2CPP metadata produced for
that exact build, and resolves every Hook target by IL2CPP method identity:

    (ScriptMethod.Name, ScriptMethod.TypeSignature)
        -> ScriptMethod.Address          (== element table slot)
        -> wastool.table_slot_to_func   (== Wasm function index)
        -> Wasm function type / calling convention
        -> normalized structural fingerprint
        -> safe patch insertion point

It also resolves the two repurposed imports and the managed string allocator
without using any hard-coded function index or element slot.

Design rules (phase 28):
  * Every required target must match exactly once. Zero / multiple matches,
    signature mismatch, structure mismatch or an unsafe patch position is a
    hard failure. There is NO "best guess" and NO fallback to historical ids.
  * Historical slots/indices are recorded for regression comparison ONLY and
    are never consulted by the resolver.
  * Resolution output is bound to the SHA-256 of every input file. A stale
    resolved_targets.json cannot be reused with a different Wasm.
  * The structural fingerprint is a SAFETY CHECK AFTER metadata resolution,
    never a fuzzy function searcher.

Two entry points:
  * `main()` / --emit-baseline : developer CLI.
  * `verify_resolution()`      : called by build_patch.py before patching.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import wastool

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
INPUTS = HERE / 'inputs'
DEFAULT_WASM = INPUTS / 'ntd.orig.wasm'
DEFAULT_SCRIPT = INPUTS / 'script.json'
DEFAULT_SLOTMAP = INPUTS / 'slot2name.json'
DEFAULT_TARGETS = HERE / 'hook_targets.json'
DEFAULT_OUTDIR = ROOT / 'build' / 'wasm'

# Tag bits used by the JS bridge protocol.  A legitimate original call must
# never pass a second argument carrying any of these bits.
PROTOCOL_TAG_MASK = 0x78000000  # 0x40000000 | 0x20000000 | 0x10000000 | 0x08000000

VALTYPE_NAME = {0x7f: 'i32', 0x7e: 'i64', 0x7d: 'f32', 0x7c: 'f64',
                0x7b: 'v128', 0x70: 'funcref', 0x6f: 'externref'}
TS_CHAR = {'v': None, 'i': 0x7f, 'j': 0x7e, 'f': 0x7d, 'd': 0x7c}

# i32.const values at or above this are treated as absolute linear-memory /
# data-section addresses.  Their numeric VALUE is normalized away (it drifts
# between builds) while the FACT that a static reference exists is retained.
DATA_ADDR_THRESHOLD = 0x100000

CONTROL_OPEN = ('block', 'loop', 'if')
MEM_OPS_PREFIX = ('.load', '.store')


class ResolutionError(Exception):
    """A required check failed.  Never caught and downgraded to a warning."""


def fail(msg: str):
    raise ResolutionError(msg)


def need(cond, msg: str):
    if not cond:
        fail(msg)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path):
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write('\n')


def wasm_sig_str(sig) -> str:
    params, results = sig
    p = '(' + ','.join(VALTYPE_NAME.get(v, hex(v)) for v in params) + ')'
    r = '(' + ','.join(VALTYPE_NAME.get(v, hex(v)) for v in results) + ')'
    return p + '->' + r


def ts_to_wasm(ts: str):
    """Il2CppDumper TypeSignature -> Wasm (params, results)."""
    need(isinstance(ts, str) and len(ts) >= 1, f'bad TypeSignature {ts!r}')
    head, rest = ts[0], ts[1:]
    if head == 'v':
        results = []
    else:
        need(head in TS_CHAR and TS_CHAR[head] is not None,
             f'unsupported TypeSignature result char {head!r} in {ts!r}')
        results = [TS_CHAR[head]]
    params = []
    for c in rest:
        need(c in TS_CHAR and TS_CHAR[c] is not None,
             f'unsupported TypeSignature param char {c!r} in {ts!r}')
        params.append(TS_CHAR[c])
    return (params, results)


def uleb_bytes(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7f
        v >>= 7
        out.append(b | 0x80 if v else b)
        if not v:
            return bytes(out)


# --------------------------------------------------------------------------
# module context + normalized structural fingerprint
# --------------------------------------------------------------------------
class ModuleContext:
    def __init__(self, w: wastool.Wasm, slot2name: dict):
        self.w = w
        self.slot2name = {int(k): v for k, v in (slot2name or {}).items()}
        self.func2slot = {}
        for off, funcs in w.elem:
            for i, f in enumerate(funcs):
                self.func2slot.setdefault(f, off + i)
        self.import_fields = {}
        k = 0
        for imp in w.imports:
            if imp['kind'] == 0:
                self.import_fields[k] = f'{imp["module"]}.{imp["name"]}'
                k += 1
        self.num_imp = w.num_imported_funcs
        self._fp_cache = {}

    def func_imports(self):
        k = 0
        for imp in self.w.imports:
            if imp['kind'] == 0:
                yield k, imp
                k += 1

    def slot_of(self, fidx):
        return self.func2slot.get(fidx)

    def call_symbol(self, target: int) -> str:
        """Semantic identity of a call target, independent of function index."""
        w = self.w
        if target < self.num_imp:
            imp = None
            for k, item in self.func_imports():
                if k == target:
                    imp = item
                    break
            field = f'{imp["module"]}.{imp["name"]}' if imp else '?'
            return f'import:{field}:{wasm_sig_str(w.types[imp["typeidx"]])}'
        sig = wasm_sig_str(w.func_index_to_type(target))
        slot = self.slot_of(target)
        name = self.slot2name.get(slot) if slot is not None else None
        di = target - self.num_imp
        size = w.code[di][1] if 0 <= di < len(w.code) else -1
        return f'def:{sig}:{name or "anon"}:bodylen={size}'

    def _norm(self, off, mn, imm, ln, nparams) -> str:
        """Normalize ONE instruction.

        Deliberately PRESERVED (semantic):
          * local.get/set/tee index (parameter number, argument order, local reuse)
          * memarg offset/align (managed field offsets)
          * all control-flow opcodes and branch labels
          * call-target semantics (signature + metadata name + body size)
          * small i32/i64 constants (field offsets, flags, tags)
          * f32/f64/prefixed/unknown opcode immediate BYTES
        Deliberately NORMALIZED (non-semantic):
          * LEB128 encoding length (we decode to numbers)
          * absolute file offsets (never emitted)
          * function indices (replaced by semantic call symbols)
          * absolute data-section addresses (value replaced by <DATA_ADDR>)
        """
        w = self.w
        if mn.startswith('??') or mn.startswith('0xfc/'):
            return f'{mn}@{w.buf[off:off + ln].hex()}'
        if mn in ('local.get', 'local.set', 'local.tee'):
            return f'{mn}@{int(imm)}'
        if mn == 'call':
            return 'call@' + self.call_symbol(int(imm))
        if mn == 'call_indirect':
            parts = dict(x.split('=', 1) for x in imm.split())
            ti = int(parts['type'])
            return (f'call_indirect@sig={wasm_sig_str(w.types[ti])}'
                    f'@table={parts["table"]}')
        if mn in ('i32.const', 'i64.const'):
            v = int(imm)
            if abs(v) >= DATA_ADDR_THRESHOLD:
                return f'{mn}@<DATA_ADDR>'
            return f'{mn}@{v}'
        if mn in ('f32.const', 'f64.const', 'select_t'):
            return f'{mn}@{w.buf[off:off + ln].hex()}'
        if imm:
            return f'{mn}@{imm}'
        return mn

    def fingerprint(self, fidx: int) -> dict:
        if fidx in self._fp_cache:
            return self._fp_cache[fidx]
        w = self.w
        di = fidx - self.num_imp
        need(0 <= di < len(w.code), f'function {fidx} has no code body')
        body_start, body_size, decls, _local_end = w.code[di]
        ins = wastool.decode(w, fidx)
        need(ins, f'function {fidx} decoded to an empty instruction list')
        nparams = len(w.func_index_to_type(fidx)[0])

        seq = [self._norm(off, mn, imm, ln, nparams) for off, mn, imm, ln in ins]

        depth = 0
        top_ends = []
        control = []
        for i, (_off, mn, imm, _ln) in enumerate(ins):
            if mn in CONTROL_OPEN:
                depth += 1
                control.append(f'{mn}@{imm}' if imm else mn)
            elif mn == 'end':
                if depth == 0:
                    top_ends.append(i)
                    control.append('end@top')
                else:
                    depth -= 1
                    control.append('end')
            elif mn in ('br', 'br_if', 'br_table', 'else', 'return', 'unreachable'):
                control.append(f'{mn}@{imm}' if imm else mn)

        mem_offsets = []
        calls = []
        params_used = set()
        static_refs = 0
        for _off, mn, imm, _ln in ins:
            if any(mn.endswith(s) or (s in mn) for s in MEM_OPS_PREFIX):
                if isinstance(imm, str) and imm.startswith('off='):
                    off_txt = imm.split()[0].split('=', 1)[1]
                    mem_offsets.append(int(off_txt))
            if mn == 'call':
                calls.append(self.call_symbol(int(imm)))
            if mn in ('local.get', 'local.set', 'local.tee'):
                v = int(imm)
                if v < nparams:
                    params_used.add(v)
            if mn == 'i32.const' and abs(int(imm)) >= DATA_ADDR_THRESHOLD:
                static_refs += 1

        last = ins[-1]
        trailing_ok = (last[1] == 'end'
                       and last[0] + last[3] == body_start + body_size
                       and len(top_ends) == 1)
        fp = {
            'fingerprint_sha256': sha256_bytes('\n'.join(seq).encode('utf-8')),
            'instruction_count': len(ins),
            'control_flow_sha256': sha256_bytes('\n'.join(control).encode('utf-8')),
            'mem_offsets': sorted(set(mem_offsets)),
            'call_targets': sorted(set(calls)),
            'param_indices_used': sorted(params_used),
            'static_ref_count': static_refs,
            'top_level_ends': len(top_ends),
            'trailing_end_ok': trailing_ok,
            'local_decls': [[int(c), int(t)] for c, t in decls],
            'body_size': body_size,
            # Diagnostic only: raw bytes contain function indices, so this
            # legitimately changes under function reordering. Never a gate.
            'raw_body_sha256': sha256_bytes(bytes(w.buf[body_start:body_start + body_size])),
        }
        self._fp_cache[fidx] = fp
        return fp

    def call_sites(self, target: int):
        """All verified direct `call <target>` sites in the ORIGINAL module."""
        pat = b'\x10' + uleb_bytes(target)
        out = []
        for di, (bs, size, _decls, _le) in enumerate(self.w.code):
            if pat not in self.w.buf[bs:bs + size]:
                continue
            gi = di + self.num_imp
            ins = wastool.decode(self.w, gi)
            hits = [k for k, x in enumerate(ins)
                    if x[1] == 'call' and x[2] == str(target)]
            if hits:
                out.append((gi, ins, hits))
        return out


# Fields of a fingerprint that are strict gates (raw_body_sha256 excluded).
GATE_FIELDS = ('fingerprint_sha256', 'instruction_count', 'control_flow_sha256',
               'mem_offsets', 'call_targets', 'param_indices_used',
               'static_ref_count', 'top_level_ends', 'trailing_end_ok',
               'local_decls', 'body_size')


def structure_diffs(expected: dict, actual: dict) -> list:
    diffs = []
    for key in GATE_FIELDS:
        if key not in expected:
            continue
        if actual.get(key) != expected[key]:
            diffs.append(f'{key}: baseline={expected[key]!r} actual={actual.get(key)!r}')
    return diffs


def compact_structure(fp: dict) -> dict:
    return {k: fp[k] for k in GATE_FIELDS}


# --------------------------------------------------------------------------
# resolution steps
# --------------------------------------------------------------------------
def unique_method(methods, name, ts):
    return [m for m in methods
            if m.get('Name') == name and m.get('TypeSignature') == ts]


def resolve_target(ctx: ModuleContext, methods, decl: dict) -> dict:
    tid = decl['id']
    name, ts = decl['method'], decl['type_signature']
    hits = unique_method(methods, name, ts)
    if len(hits) != 1:
        preview = [{'Address': m.get('Address'), 'Name': m.get('Name'),
                    'TypeSignature': m.get('TypeSignature')} for m in hits[:8]]
        fail(f'{tid}: (Name, TypeSignature) matched {len(hits)} ScriptMethod '
             f'entries, expected exactly 1. candidates={preview}')

    m = hits[0]
    if decl.get('csharp_signature') and m.get('Signature') != decl['csharp_signature']:
        fail(f'{tid}: C# signature changed.\n  baseline={decl["csharp_signature"]!r}'
             f'\n  actual  ={m.get("Signature")!r}')

    address = m['Address']
    slot = address                       # phase 27: ScriptMethod.Address == elem slot
    w = ctx.w
    max_slot = sum(len(f) for _off, f in w.elem)
    need(0 <= slot < max_slot, f'{tid}: Address {slot} outside element table')
    fidx = w.table_slot_to_func(slot)
    need(fidx is not None, f'{tid}: element slot {slot} has no function')
    need(fidx >= ctx.num_imp, f'{tid}: resolved to an import ({fidx}), not a defined function')
    need(ctx.slot_of(fidx) == slot,
         f'{tid}: element slot {slot} does not map back to function {fidx}')

    expected_sig = ts_to_wasm(ts)
    actual_sig = w.func_index_to_type(fidx)
    need(list(actual_sig[0]) == list(expected_sig[0])
         and list(actual_sig[1]) == list(expected_sig[1]),
         f'{tid}: Wasm type {wasm_sig_str(actual_sig)} != '
         f'TypeSignature {ts} {wasm_sig_str(expected_sig)}')
    if decl.get('expected_params'):
        need(len(actual_sig[0]) == len(decl['expected_params']),
             f'{tid}: parameter count {len(actual_sig[0])} != '
             f'{len(decl["expected_params"])}')
    if decl.get('expected_results') is not None:
        need(len(actual_sig[1]) == len(decl['expected_results']),
             f'{tid}: result count mismatch')

    fp = ctx.fingerprint(fidx)
    baseline = decl.get('structure') or {}
    diffs = structure_diffs(baseline, fp) if baseline else []
    if baseline and diffs:
        fail(f'{tid}: structural fingerprint mismatch (compiler change? manual review required):\n    '
             + '\n    '.join(diffs))
    if not baseline:
        fail(f'{tid}: no structural baseline in hook_targets.json; '
             f'run resolve_targets.py --emit-baseline after manual review')

    # patch-position safety -------------------------------------------------
    constraints = decl.get('constraints') or {}
    for idx in decl.get('required_param_indices', []):
        need(int(idx) < len(actual_sig[0]),
             f'{tid}: Hook needs parameter {idx} but function has {len(actual_sig[0])} params')
    if constraints.get('has_static_data_reference'):
        need(fp['static_ref_count'] > 0,
             f'{tid}: expected at least one static/data reference, found none')
    for op in constraints.get('must_contain_opcodes', []):
        ins = wastool.decode(w, fidx)
        need(any(x[1] == op for x in ins),
             f'{tid}: required opcode {op!r} missing from function body')
    if constraints.get('tail_end_unique'):
        need(fp['trailing_end_ok'] and fp['top_level_ends'] == 1,
             f'{tid}: cannot confirm a single trailing `end` for tail insertion')
    need(fp['trailing_end_ok'], f'{tid}: function body has no single final `end`')

    return {
        'id': tid,
        'method': name,
        'type_signature': ts,
        'metadata_hits': len(hits),
        'metadata_address': address,
        'element_slot': slot,
        'func_index': fidx,
        'def_index': fidx - ctx.num_imp,
        'wasm_signature': {
            'params': [VALTYPE_NAME.get(v, hex(v)) for v in actual_sig[0]],
            'results': [VALTYPE_NAME.get(v, hex(v)) for v in actual_sig[1]],
        },
        'patch_logic': decl['patch_logic'],
        'patch_position': decl['patch_position'],
        'required_param_indices': decl.get('required_param_indices', []),
        'structure': fp,
        'checks': {
            'metadata_unique': True,
            'csharp_signature': bool(decl.get('csharp_signature')),
            'element_slot_valid': True,
            'wasm_type_matches': True,
            'structure_matches_baseline': True,
            'patch_position_safe': True,
        },
        'historical': decl.get('historical'),
        'required': bool(decl.get('required', True)),
        'status': 'PASS',
    }


def resolve_import(ctx: ModuleContext, decl: dict) -> dict:
    iid = decl['id']
    module, field = decl['module'], decl['field']
    matches = [(k, imp) for k, imp in ctx.func_imports()
               if imp['module'] == module and imp['name'] == field]
    need(len(matches) == 1,
         f'{iid}: (module,field)=({module!r},{field!r}) matched {len(matches)} '
         f'imports, expected exactly 1')
    idx, imp = matches[0]
    sig = ctx.w.types[imp['typeidx']]
    expected = ts_to_wasm(decl['type_signature'])
    need(list(sig[0]) == list(expected[0]) and list(sig[1]) == list(expected[1]),
         f'{iid}: import type {wasm_sig_str(sig)} != expected '
         f'{wasm_sig_str(expected)}')

    sites = ctx.call_sites(idx)
    need(len(sites) == decl['expected_call_sites'],
         f'{iid}: {len(sites)} original call site(s), expected '
         f'{decl["expected_call_sites"]}. The single-call-site protocol '
         f'precondition changed -> manual review required')
    gi, ins, hits = sites[0]
    k = hits[0]

    # Protocol safety: the ORIGINAL call's second argument must be a plain
    # constant with no protocol tag bits, otherwise our tag protocol would
    # hijack a legitimate game call.
    arg2 = None
    for j in range(k - 1, max(-1, k - 5), -1):
        mn, imm = ins[j][1], ins[j][2]
        if mn == 'i32.const':
            arg2 = int(imm)
            break
        if mn in ('call', 'end', 'br', 'drop'):
            break
    need(arg2 is not None,
         f'{iid}: cannot identify the original second argument constant '
         f'(protocol safety cannot be proven)')
    need((arg2 & PROTOCOL_TAG_MASK) == 0,
         f'{iid}: original second argument 0x{arg2:x} overlaps protocol tag bits')

    site_fp = ctx.fingerprint(gi)
    cs = decl.get('expected_call_site') or {}
    baseline = cs.get('structure') or {}
    diffs = structure_diffs(baseline, site_fp) if baseline else []
    if baseline and diffs:
        fail(f'{iid}: call-site function structure changed -> manual review required:\n    '
             + '\n    '.join(diffs))
    # Call-site IDENTITY is proven by signature + structural fingerprint + IL2CPP
    # name, NOT by the element slot (the slot legitimately changes when functions
    # are reordered).  The slot is recorded as evidence only.
    if cs.get('wasm_signature'):
        need(wasm_sig_str(ctx.w.func_index_to_type(gi)) == cs['wasm_signature'],
             f'{iid}: call-site signature changed')
    expected_name = cs.get('il2cpp_name')
    if expected_name is not None:
        need(ctx.slot2name.get(ctx.slot_of(gi)) == expected_name,
             f'{iid}: call-site IL2CPP identity changed '
             f'({ctx.slot2name.get(ctx.slot_of(gi))!r} != {expected_name!r})')
    if cs.get('element_slot') is not None:
        need(ctx.slot_of(gi) is not None,
             f'{iid}: call-site function is no longer in the element table')
    need(arg2 == cs.get('second_argument_const', arg2),
         f'{iid}: original second argument constant changed '
         f'{cs.get("second_argument_const")} -> {arg2}')

    return {
        'id': iid, 'role': decl['role'], 'module': module, 'field': field,
        'import_index': idx, 'type_index': imp['typeidx'],
        'wasm_signature': wasm_sig_str(sig),
        'metadata_hits': len(matches),
        'original_call_sites': len(sites),
        'call_site': {
            'func_index': gi,
            'def_index': gi - ctx.num_imp,
            'element_slot': ctx.slot_of(gi),
            'il2cpp_name': ctx.slot2name.get(ctx.slot_of(gi)),
            'type': wasm_sig_str(ctx.w.func_index_to_type(gi)),
            'second_argument_const': arg2,
            'structure': site_fp,
        },
        'protocol_safety': {
            'tag_mask': f'0x{PROTOCOL_TAG_MASK:08x}',
            'original_arg2_has_tag_bits': False,
            'single_original_call_site': True,
        },
        'historical': decl.get('historical'),
        'checks': {
            'module_field_unique': True, 'type_matches': True,
            'single_call_site': True, 'call_site_structure': True,
            'protocol_safe': True,
        },
        'status': 'PASS',
    }


def resolve_allocator(ctx: ModuleContext, methods, decl: dict) -> dict:
    w = ctx.w
    hits = unique_method(methods, decl['wrapper_method'], decl['wrapper_type_signature'])
    need(len(hits) == 1,
         f'allocator: wrapper {decl["wrapper_method"]!r}/'
         f'{decl["wrapper_type_signature"]!r} matched {len(hits)} entries, expected 1')
    slot = hits[0]['Address']
    wrapper = w.table_slot_to_func(slot)
    need(wrapper is not None, f'allocator: wrapper slot {slot} has no function')
    need(wrapper >= ctx.num_imp, 'allocator: wrapper resolved to an import')

    wins = wastool.decode(w, wrapper)
    pattern = [(x[1], x[2]) for x in wins]
    thin_ok = (len(pattern) == 3
               and pattern[0] == ('local.get', '0')
               and pattern[1][0] == 'call'
               and pattern[2] == ('end', ''))
    need(thin_ok,
         f'allocator: FastAllocateString is no longer the 3-instruction thin '
         f'wrapper local.get 0; call X; end (got {pattern})')
    need(w.code[wrapper - ctx.num_imp][2] == [],
         'allocator: wrapper unexpectedly declares local variables')
    need(list(w.func_index_to_type(wrapper)) ==
         [list(ts_to_wasm(decl['wrapper_type_signature'])[0]),
          list(ts_to_wasm(decl['wrapper_type_signature'])[1])],
         'allocator: wrapper Wasm type mismatch')
    wfp = ctx.fingerprint(wrapper)
    wdiffs = structure_diffs(decl.get('wrapper_structure') or {}, wfp) \
        if decl.get('wrapper_structure') else []
    need(not wdiffs, 'allocator: wrapper structure changed:\n    ' + '\n    '.join(wdiffs))

    target = int(wins[1][2])
    need(target >= ctx.num_imp,
         f'allocator: thin wrapper calls import {target}, not a defined function')
    sig = w.func_index_to_type(target)
    expected = ts_to_wasm(decl['type_signature'])
    need(list(sig[0]) == list(expected[0]) and list(sig[1]) == list(expected[1]),
         f'allocator: target type {wasm_sig_str(sig)} != expected '
         f'{wasm_sig_str(expected)}')

    afp = ctx.fingerprint(target)
    adiffs = structure_diffs(decl.get('structure') or {}, afp) \
        if decl.get('structure') else []
    need(not adiffs, 'allocator: String::NewSize structure changed:\n    ' + '\n    '.join(adiffs))

    ains = wastool.decode(w, target)
    stores = {x[1]: x[2] for x in ains if x[1] in ('i32.store', 'i32.store16')}
    need(any(v.startswith('off=8') for v in stores.values()),
         'allocator: no i32.store off=8 (Il2CppString.length @ +8)')
    need(any(v.startswith('off=12') for v in stores.values()),
         'allocator: no i32.store16 off=12 (NUL terminator @ chars)')
    need(any(x[1] == 'call' for x in ains),
         'allocator: no GC allocation call inside the allocator')
    need(afp['static_ref_count'] > 0,
         'allocator: no static/data reference (String::Empty / klass) found')

    callers = ctx.call_sites(target)
    need(len(callers) >= 1, 'allocator: allocator has no direct callers')
    in_elem = target in ctx.func2slot
    expected_in_elem = not bool(decl.get('not_in_element_table', True))
    need(in_elem == expected_in_elem,
         f'allocator: element-table presence changed (in_elem={in_elem}, '
         f'expected {expected_in_elem}); the wrapper-export rationale must be re-reviewed')

    return {
        'id': 'F1', 'role': 'il2cpp::vm::String::NewSize(int32)',
        'wrapper': {
            'method': decl['wrapper_method'],
            'type_signature': decl['wrapper_type_signature'],
            'metadata_hits': len(hits), 'element_slot': slot,
            'func_index': wrapper, 'def_index': wrapper - ctx.num_imp,
            'disasm': pattern, 'structure': wfp,
        },
        'func_index': target,
        'def_index': target - ctx.num_imp,
        'wasm_signature': wasm_sig_str(sig),
        'in_element_table': in_elem,
        'direct_callers': len(callers),
        'layout': {'string_length_off': 8, 'string_chars_off': 12,
                   'store_length': stores.get('i32.store'),
                   'store_nul': stores.get('i32.store16')},
        'structure': afp,
        'historical': decl.get('historical'),
        'checks': {
            'wrapper_unique': True, 'wrapper_thin': True,
            'target_type_matches': True, 'target_structure': True,
            'string_layout_operations': True, 'gc_allocation_call': True,
            'callers_exist': True,
        },
        'status': 'PASS',
    }


def resolve_appended(ctx: ModuleContext, cfg: dict) -> dict:
    base = ctx.w.num_imported_funcs + len(ctx.w.func_types)
    items = []
    exports = {}
    for offset, item in enumerate(cfg['appended_functions']):
        idx = base + offset
        rec = dict(item)
        rec['func_index'] = idx
        rec['def_index'] = idx - ctx.num_imp
        items.append(rec)
        if item.get('export'):
            need(item['export'] not in exports,
                 f'duplicate export name {item["export"]}')
            exports[item['export']] = idx
    need(len(exports) == len(cfg.get('exports', {})),
         'export count mismatch between appended_functions and exports declaration')
    for name, expected_idx in cfg.get('exports', {}).items():
        need(name in exports, f'declared export {name!r} has no appended function')
    return {
        'base_formula': 'imported_function_count + defined_function_count + appended_function_offset',
        'imported_function_count': ctx.num_imp,
        'defined_function_count': len(ctx.w.func_types),
        'base': base,
        'count': len(items),
        'functions': items,
        'exports': exports,
    }


# --------------------------------------------------------------------------
# build-time re-verification (used by build_patch.py)
# --------------------------------------------------------------------------
def verify_resolution(w: wastool.Wasm, resolved: dict, targets_cfg: dict,
                      wasm_path: Path, targets_path: Path) -> None:
    """Re-prove that `resolved` still describes THIS Wasm before patching.

    Never falls back to historical numbers; any mismatch aborts the build.
    """
    inputs = resolved.get('inputs', {})
    need(inputs.get('wasm', {}).get('sha256') == sha256_bytes(w.buf),
         'resolved_targets.json is bound to a DIFFERENT Wasm (SHA-256 mismatch). '
         'Re-run resolve_targets.py; refusing to patch.')
    need(inputs.get('hook_targets', {}).get('sha256') == sha256_file(targets_path),
         'hook_targets.json changed since resolution. Re-run resolve_targets.py.')
    sj = inputs.get('script_json', {})
    if sj.get('path') and Path(sj['path']).is_file():
        need(sj.get('sha256') == sha256_file(Path(sj['path'])),
             'IL2CPP script.json changed since resolution. Re-run resolve_targets.py.')
    need(resolved.get('status') == 'PASS',
         'resolved_targets.json status is not PASS; refusing to patch.')

    slotmap_path = inputs.get('slot2name', {}).get('path')
    slot2name = load_json(Path(slotmap_path)) if slotmap_path and Path(slotmap_path).is_file() else {}
    ctx = ModuleContext(w, slot2name)
    decl_by_id = {d['id']: d for d in targets_cfg['targets']}

    for rec in resolved['targets']:
        d = decl_by_id[rec['id']]
        need(recheck_target_identity(ctx, rec, d),
             f'{rec["id"]}: re-verification of slot/type failed')
        fp = ctx.fingerprint(rec['func_index'])
        need(not structure_diffs(d.get('structure') or {}, fp),
             f'{rec["id"]}: structure changed since resolution')
        need(ctx.slot_of(rec['func_index']) == rec['element_slot'],
             f'{rec["id"]}: element slot changed since resolution')
    for key in ('lookup', 'context'):
        rec = resolved['imports'][key]
        need(len(ctx.call_sites(rec['import_index'])) == rec['original_call_sites'],
             f'import {key}: call sites changed since resolution')
        need(not structure_diffs(
                rec['call_site'].get('structure', {}),
                ctx.fingerprint(rec['call_site']['func_index'])),
             f'import {key}: call-site structure changed since resolution')
    alloc = resolved['allocator']
    need(not structure_diffs(alloc.get('wrapper', {}).get('structure', {}),
                             ctx.fingerprint(alloc['wrapper']['func_index'])),
         'allocator wrapper structure changed since resolution')
    need(not structure_diffs(alloc.get('structure', {}),
                             ctx.fingerprint(alloc['func_index'])),
         'allocator structure changed since resolution')
    need(resolved['appended']['base'] == w.num_imported_funcs + len(w.func_types),
         'appended function base no longer matches this module')


def recheck_target_identity(ctx, rec, decl) -> bool:
    """Re-check slot/type consistency without re-parsing script.json."""
    w = ctx.w
    sig = w.func_index_to_type(rec['func_index'])
    expected = ts_to_wasm(decl['type_signature'])
    return (list(sig[0]) == list(expected[0])
            and list(sig[1]) == list(expected[1])
            and w.table_slot_to_func(rec['element_slot']) == rec['func_index'])


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def render_report(resolved: dict) -> str:
    L = []
    L.append('# Wasm Hook 动态定位报告')
    L.append('')
    L.append(f"- 生成工具：`tools/wasm/resolve_targets.py`")
    L.append(f"- 游戏构建：`{resolved['inputs'].get('game_build')}`")
    L.append(f"- 原始 Wasm SHA-256：`{resolved['inputs']['wasm']['sha256']}`")
    L.append(f"- script.json SHA-256：`{resolved['inputs']['script_json']['sha256']}`")
    L.append(f"- hook_targets.json SHA-256：`{resolved['inputs']['hook_targets']['sha256']}`")
    L.append(f"- 总结论：**{resolved['status']}**（{resolved['checks_summary']}）")
    L.append('')
    L.append('> 定位主键是 `(ScriptMethod.Name, TypeSignature)`。`historical` 列只作回归对照，'
             '不参与任何定位决策。结构指纹是定位之后的安全确认，不是模糊搜索。')
    L.append('')
    L.append('## 1. 9 个 Hook 目标')
    L.append('')
    L.append('| Hook | 方法 | TS | 元数据命中 | 当前 slot / func / def | 历史 slot / func | Wasm 签名 | 结构校验 | 插入位置 | 依赖 | 结果 |')
    L.append('|---|---|---|---|---|---|---|---|---|---|---|')
    for t in resolved['targets']:
        h = t['historical'] or {}
        hist = f"{h.get('slot','-')} / {h.get('func_index','-')}"
        cur = f"0x{t['element_slot']:x} / {t['func_index']} / {t['def_index']}"
        sig = '(' + ','.join(t['wasm_signature']['params']) + ')->(' + \
              ','.join(t['wasm_signature']['results']) + ')'
        deps = t['patch_logic']
        L.append(f"| {t['id']} | `{t['method']}` | `{t['type_signature']}` | "
                 f"{t['metadata_hits']} | {cur} | {hist} | `{sig}` | "
                 f"指纹 `{t['structure']['fingerprint_sha256'][:12]}…` / "
                 f"{t['structure']['instruction_count']} 指令 | "
                 f"{t['patch_position']}, params={t['required_param_indices']} | "
                 f"{deps} | **{t['status']}** |")
    L.append('')
    L.append('## 2. 两个复用 import')
    L.append('')
    L.append('| 角色 | module.field | 当前索引 | 历史索引 | 类型 | 原调用点 | 调用点函数 | 原第二参数 | tag 冲突 | 结果 |')
    L.append('|---|---|---|---|---|---|---|---|---|---|')
    for key in ('lookup', 'context'):
        r = resolved['imports'][key]
        h = r.get('historical') or {}
        cs = r['call_site']
        L.append(f"| {r['role']} | `{r['module']}.{r['field']}` | {r['import_index']} | "
                 f"{h.get('import_index','-')} | `{r['wasm_signature']}` | "
                 f"{r['original_call_sites']} 处 | func {cs['func_index']} "
                 f"(slot {cs['element_slot']}, {cs['il2cpp_name'] or 'no IL2CPP name'}) | "
                 f"0x{cs['second_argument_const']:x} | 无 | **{r['status']}** |")
    L.append('')
    L.append('## 3. 字符串分配函数')
    L.append('')
    a = resolved['allocator']
    h = a.get('historical') or {}
    L.append(f"- 包装方法：`{a['wrapper']['method']}` / `{a['wrapper']['type_signature']}`，"
             f"元数据命中 {a['wrapper']['metadata_hits']}，slot 0x{a['wrapper']['element_slot']:x}，"
             f"func {a['wrapper']['func_index']}")
    L.append(f"- 薄包装反汇编：`{a['wrapper']['disasm']}`")
    L.append(f"- 分配函数：func **{a['func_index']}**（历史 {h.get('func_index','-')}），"
             f"类型 `{a['wasm_signature']}`，调用它的函数 {a['direct_callers']} 个，"
             f"在 elem 表中：{a['in_element_table']}")
    L.append(f"- 字符串布局操作：length `{a['layout']['store_length']}`、"
             f"NUL `{a['layout']['store_nul']}`")
    L.append(f"- 结构指纹：`{a['structure']['fingerprint_sha256']}`")
    L.append(f"- 结果：**{a['status']}**")
    L.append('')
    L.append('## 4. 追加函数与导出')
    L.append('')
    ap = resolved['appended']
    L.append(f"- 编号公式：`{ap['base_formula']}` = {ap['imported_function_count']} + "
             f"{ap['defined_function_count']} + offset，base = **{ap['base']}**")
    L.append('')
    L.append('| 偏移 | 名称 | func index | 签名 | 导出 |')
    L.append('|---:|---|---:|---|---|')
    for i, f in enumerate(ap['functions']):
        L.append(f"| {i} | `{f['id']}` | {f['func_index']} | `{f['type_signature']}` | "
                 f"{('`' + f['export'] + '`') if f.get('export') else '—'} |")
    L.append('')
    L.append('## 5. 模块结构')
    L.append('')
    m = resolved['module']
    L.append(f"- imported funcs {m['imported_functions']} / defined funcs "
             f"{m['defined_functions']} / types {m['types']} / exports {m['exports']} / "
             f"element slots {m['element_slots']} ({m['element_segments']} 段)")
    L.append('')
    L.append('## 6. 本轮未解决的剩余依赖（官方更新时仍需人工介入）')
    L.append('')
    for item in resolved.get('remaining_dependencies', []):
        L.append(f'- {item}')
    L.append('')
    L.append('> 本阶段只解决 Wasm Hook 的动态定位与重新生成。**不能**据此宣称整个汉化系统'
             '已兼容新版游戏；尤其不能把旧 Wasm 补丁重定向给结构不同的新版游戏。')
    L.append('')
    return '\n'.join(L)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--wasm', default=str(DEFAULT_WASM), help='official UNPATCHED ntd.wasm')
    p.add_argument('--script-json', default=str(DEFAULT_SCRIPT))
    p.add_argument('--slot-map', default=str(DEFAULT_SLOTMAP))
    p.add_argument('--targets', default=str(DEFAULT_TARGETS))
    p.add_argument('--out-dir', default=str(DEFAULT_OUTDIR))
    p.add_argument('--game-build', default=None)
    p.add_argument('--emit-baseline', action='store_true',
                   help='(manual review only) recompute structural baselines into '
                        'hook_targets.json, then exit without resolving')
    return p


def emit_baseline(args):
    """Recompute structural baselines for the CURRENT official build.

    This is an EXPLICIT human-reviewed action, never an automatic fallback.
    """
    wasm_path, script_path = Path(args.wasm), Path(args.script_json)
    slot_path, targets_path = Path(args.slot_map), Path(args.targets)
    for p in (wasm_path, script_path, targets_path):
        need(p.is_file(), f'missing input: {p}')
    w = wastool.Wasm(str(wasm_path))
    slot2name = load_json(slot_path) if slot_path.is_file() else {}
    ctx = ModuleContext(w, slot2name)
    methods = load_json(script_path).get('ScriptMethod')
    need(isinstance(methods, list), 'script.json has no ScriptMethod array')
    cfg = load_json(targets_path)

    changes = []
    for decl in cfg['targets']:
        hits = unique_method(methods, decl['method'], decl['type_signature'])
        need(len(hits) == 1,
             f'{decl["id"]}: cannot emit baseline, {len(hits)} metadata matches')
        f = w.table_slot_to_func(hits[0]['Address'])
        need(f is not None, f'{decl["id"]}: Address not in element table')
        decl['structure'] = compact_structure(ctx.fingerprint(f))
        hist = decl.setdefault('historical', {})
        changes.append(f"{decl['id']}: slot 0x{hits[0]['Address']:x} func {f} "
                       f"fp={decl['structure']['fingerprint_sha256'][:12]}")
    for imp in cfg['imports']:
        matches = [(k, x) for k, x in ctx.func_imports()
                   if x['module'] == imp['module'] and x['name'] == imp['field']]
        need(len(matches) == 1, f"{imp['id']}: import not unique")
        idx = matches[0][0]
        sites = ctx.call_sites(idx)
        need(len(sites) == 1, f"{imp['id']}: expected 1 call site, got {len(sites)}")
        gi, ins, hits = sites[0]
        k = hits[0]
        arg2 = None
        for j in range(k - 1, max(-1, k - 5), -1):
            if ins[j][1] == 'i32.const':
                arg2 = int(ins[j][2]); break
            if ins[j][1] in ('call', 'end', 'br', 'drop'):
                break
        cs = imp.setdefault('expected_call_site', {})
        cs['structure'] = compact_structure(ctx.fingerprint(gi))
        cs['element_slot'] = ctx.slot_of(gi)
        cs['il2cpp_name'] = ctx.slot2name.get(ctx.slot_of(gi))
        cs['wasm_signature'] = wasm_sig_str(w.func_index_to_type(gi))
        cs['second_argument_const'] = arg2
        imp.setdefault('historical', {})['import_index'] = idx
        changes.append(f"{imp['id']}: import {idx} call-site func {gi} arg2={arg2}")
    a = cfg['allocator']
    hits = unique_method(methods, a['wrapper_method'], a['wrapper_type_signature'])
    need(len(hits) == 1, 'allocator wrapper metadata not unique')
    wf = w.table_slot_to_func(hits[0]['Address'])
    wins = wastool.decode(w, wf)
    need(len(wins) == 3 and wins[1][1] == 'call', 'wrapper is not a thin call wrapper')
    target = int(wins[1][2])
    a['wrapper_structure'] = compact_structure(ctx.fingerprint(wf))
    a['structure'] = compact_structure(ctx.fingerprint(target))
    a['not_in_element_table'] = target not in ctx.func2slot
    a.setdefault('historical', {}).update({
        'func_index': target, 'wrapper_func_index': wf,
        'wrapper_element_slot': hits[0]['Address'],
    })
    changes.append(f"F1: allocator func {target} fp={a['structure']['fingerprint_sha256'][:12]}")
    cfg['baseline'] = {
        'note': 'Structural baselines for the CURRENT official build. Generated by '
                'resolve_targets.py --emit-baseline after manual review. A new game '
                'build that changes structure MUST be reviewed by a human before '
                'these values are regenerated; this tool never auto-relaxes them.',
        'wasm_sha256': sha256_bytes(w.buf),
        'script_json_sha256': sha256_file(script_path),
        'slot2name_sha256': sha256_file(slot_path) if slot_path.is_file() else None,
    }
    write_json(targets_path, cfg)
    print('BASELINE EMITTED into', targets_path)
    for c in changes:
        print('  ', c)
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.emit_baseline:
            return emit_baseline(args)
        wasm_path = Path(args.wasm)
        script_path = Path(args.script_json)
        slot_path = Path(args.slot_map)
        targets_path = Path(args.targets)
        outdir = Path(args.out_dir)
        for p in (wasm_path, script_path, targets_path):
            need(p.is_file(), f'missing input: {p}')

        w = wastool.Wasm(str(wasm_path))
        slot2name = load_json(slot_path) if slot_path.is_file() else {}
        ctx = ModuleContext(w, slot2name)
        script = load_json(script_path)
        methods = script.get('ScriptMethod')
        need(isinstance(methods, list), 'script.json has no ScriptMethod array')
        cfg = load_json(targets_path)

        targets = [resolve_target(ctx, methods, d) for d in cfg['targets']]
        seen = {}
        for t in targets:
            need(t['def_index'] not in seen,
                 f"{t['id']} and {seen.get(t['def_index'])} collapsed into the "
                 f'same function body (def {t["def_index"]})')
            seen[t['def_index']] = t['id']
        imports = {}
        imp_decls = {d['id']: d for d in cfg['imports']}
        for key, iid in (('lookup', 'I1'), ('context', 'I2')):
            imports[key] = resolve_import(ctx, imp_decls[iid])
        need(imports['lookup']['import_index'] != imports['context']['import_index'],
             'both protocol imports resolved to the same import')
        allocator = resolve_allocator(ctx, methods, cfg['allocator'])
        appended = resolve_appended(ctx, cfg)

        resolved = {
            'schema': 1,
            'generator': 'tools/wasm/resolve_targets.py',
            'status': 'PASS',
            'inputs': {
                'game_build': args.game_build or cfg.get('game_build'),
                'wasm': {'path': str(wasm_path.resolve()),
                         'sha256': sha256_bytes(w.buf), 'size': len(w.buf)},
                'script_json': {'path': str(script_path.resolve()),
                                'sha256': sha256_file(script_path),
                                'size': script_path.stat().st_size},
                'slot2name': {'path': str(slot_path.resolve()),
                              'sha256': sha256_file(slot_path) if slot_path.is_file() else None},
                'hook_targets': {'path': str(targets_path.resolve()),
                                 'sha256': sha256_file(targets_path)},
            },
            'module': {
                'imported_functions': ctx.num_imp,
                'defined_functions': len(w.func_types),
                'types': len(w.types),
                'exports': len(w.exports),
                'element_segments': len(w.elem),
                'element_slots': sum(len(f) for _o, f in w.elem),
                'tables': w.tables,
                'data_segments': len(w.data),
            },
            'targets': targets,
            'imports': imports,
            'allocator': allocator,
            'appended': appended,
            'protocol': cfg.get('protocol_reference', {}),
            'remaining_dependencies': cfg.get('remaining_dependencies', []),
            'checks_summary': (f"{len(targets)} hooks + 2 imports + 1 allocator "
                               f"+ {appended['count']} appended functions: all PASS"),
        }
        outdir.mkdir(parents=True, exist_ok=True)
        write_json(outdir / 'resolved_targets.json', resolved)
        (outdir / 'HOOK_RESOLUTION_REPORT.md').write_text(
            render_report(resolved), encoding='utf-8')
        print('RESOLUTION PASS')
        print('  wasm sha256      :', resolved['inputs']['wasm']['sha256'])
        print('  script.json sha  :', resolved['inputs']['script_json']['sha256'][:16] + '...')
        for t in targets:
            print(f"  {t['id']:3s} slot 0x{t['element_slot']:<8x} func {t['func_index']:<7d} "
                  f"def {t['def_index']:<7d} fp {t['structure']['fingerprint_sha256'][:12]}")
        for key in ('lookup', 'context'):
            r = imports[key]
            print(f"  {r['id']:3s} import {r['import_index']:<4d} "
                  f"call-site func {r['call_site']['func_index']}")
        print(f"  F1  allocator {allocator['func_index']} (wrapper "
              f"{allocator['wrapper']['func_index']})")
        print(f"  appended base {appended['base']}, exports {appended['exports']}")
        print('  ->', outdir / 'resolved_targets.json')
        print('  ->', outdir / 'HOOK_RESOLUTION_REPORT.md')
        return 0
    except ResolutionError as e:
        print('RESOLUTION FAILED:', e, file=sys.stderr)
        outdir = Path(args.out_dir)
        stale = outdir / 'resolved_targets.json'
        if stale.is_file():
            stale.unlink()                  # never leave a stale resolution behind
            print('  removed stale resolved_targets.json', file=sys.stderr)
        (outdir / 'HOOK_RESOLUTION_REPORT.md').write_text(
            '# Wasm Hook 动态定位报告\n\n**结论：FAIL**\n\n'
            f'```\n{e}\n```\n\n未生成 `resolved_targets.json`，补丁构建必须停止。\n',
            encoding='utf-8')
        return 1
    except Exception as e:
        print('RESOLUTION ERROR:', type(e).__name__, e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
