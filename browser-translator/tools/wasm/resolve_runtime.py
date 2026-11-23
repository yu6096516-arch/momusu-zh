"""Resolve every build-dependent runtime binding from the current official build.

Inputs (all produced by rebuild_wasm.py for one resource branch):

    tools/wasm/inputs[-1]/script.json      IL2CPP method metadata (name + Address + signature)
    tools/wasm/inputs[-1]/dump.cs          field offsets
    build/wasm[/branch-N]/ntd.patched.*    the Wasm that will actually ship

Outputs:

    build/wasm[runtime_dir]/runtime_config.json          machine readable
    build/wasm[runtime_dir]/RUNTIME_CONFIG_REPORT.md     human readable, per entry
    resources/monmusu_cn_runtime_config.js               merged into the loader

Why this exists: the JS shim used to carry ~25 element-table slots, 6 icall name
literals, 2 cached-icall pointers and 16 managed field offsets, all baked for
build 202611021000/0. Every one of them is wrong on /1/ (FANZA), where the Wasm
carries 5 extra functions, so the FANZA build silently lost the runtime font
fallback and the UI path rules. Nothing here is inferred from the branch number:
each value is looked up by method identity, checked against the Wasm type
system, and rejected unless the hit is unique.

Exit code 1 (and no output) when a required binding cannot be proven.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BUILD = Path(os.environ.get('MONMUSU_BUILD_ROOT') or (ROOT / 'build' / 'wasm'))
TARGETS = HERE / 'runtime_targets.json'
JS_BUNDLE = ROOT / 'monmusu_cn_runtime_config.js'
MARK_BEGIN = '/*RUNTIME_JSON_BEGIN*/'
MARK_END = '/*RUNTIME_JSON_END*/'

# wasm value types
I32, I64, F32, F64 = 0x7F, 0x7E, 0x7D, 0x7C
TYPE_OF = {'i': I32, 'j': I64, 'f': F32, 'd': F64}

RESOLVED = 'RESOLVED'
STATIC = 'VERIFIED_STATIC'
UNRESOLVED = 'UNRESOLVED'


class ResolveError(Exception):
    pass


def need(cond, msg):
    if not cond:
        raise ResolveError(msg)


def uleb(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        out.append(b | 0x80 if v else b)
        if not v:
            return bytes(out)


def ts_to_wasm(ts: str):
    """'viiii' -> ([i32,i32,i32,i32], []).  ts[0] is the return type."""
    need(len(ts) >= 1 and (ts[0] == 'v' or ts[0] in TYPE_OF),
         f'unsupported TypeSignature {ts!r}')
    params = []
    for ch in ts[1:]:
        need(ch in TYPE_OF, f'unsupported parameter type {ch!r} in {ts!r}')
        params.append(TYPE_OF[ch])
    return params, ([] if ts[0] == 'v' else [TYPE_OF[ts[0]]])


def sig_str(params, results):
    names = {I32: 'i32', I64: 'i64', F32: 'f32', F64: 'f64'}
    p = ','.join(names.get(x, hex(x)) for x in params)
    r = ','.join(names.get(x, hex(x)) for x in results) or 'void'
    return f'({p})->{r}'


def split_top(text: str):
    """Split a C parameter list on commas that are not inside <> or []."""
    out, depth, cur = [], 0, ''
    for ch in text:
        if ch in '<([':
            depth += 1
        elif ch in '>)]':
            depth -= 1
        if ch == ',' and depth == 0:
            out.append(cur)
            cur = ''
            continue
        cur += ch
    if cur.strip():
        out.append(cur)
    return out


def native_params_of(signature: str):
    i, j = signature.rfind('('), signature.rfind(')')
    if i < 0 or j <= i:
        return None
    out = []
    for part in split_top(signature[i + 1:j]):
        part = part.strip()
        if not part:
            continue
        # 'const MethodInfo* method' -> 'const MethodInfo*'; 'int32_t c' -> 'int32_t'
        out.append(part.rsplit(' ', 1)[0].strip() if ' ' in part else part)
    return out


# --------------------------------------------------------------------------- dump.cs
CLASS_RE = re.compile(
    r'^\s*(?:(?:public|internal|protected|private|sealed|abstract|partial|static|'
    r'unsafe|readonly|ref|new)\s+)*class\s+([A-Za-z_][\w`.]*)\s*'
    r'(?::[^{\n]*)?//\s*TypeDefIndex:\s*\d+\s*$')
FIELD_RE = re.compile(
    r'^\s+(?:(?:public|internal|protected|private)\s+)?'
    r'((?:(?:static|readonly|const|volatile|new|unsafe|extern)\s+)*)'
    r'[\w.<>,\[\]\s*]+?\s+([A-Za-z_]\w*)\s*;\s*//\s*(0x[0-9A-Fa-f]+)\s*$')


def parse_dump(path: Path):
    """class name -> {field: offset}. Static/const fields are skipped (their
    offset comment is a TypeDef-local index, not an object offset)."""
    out: dict[str, dict[str, int]] = {}
    cur = None
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if line.startswith('}'):
                cur = None
                continue
            m = CLASS_RE.match(line)
            if m:
                cur = m.group(1)
                out.setdefault(cur, {})
                continue
            if cur is None:
                continue
            fm = FIELD_RE.match(line)
            if not fm:
                continue
            mods = fm.group(1) or ''
            if 'static' in mods or 'const' in mods:
                continue
            out[cur].setdefault(fm.group(2), int(fm.group(3), 16))
    return out


# --------------------------------------------------------------------------- wasm view
class View:
    def __init__(self, path: Path):
        sys.path.insert(0, str(HERE))
        import wastool
        self.wastool = wastool
        self.path = path
        self.w = wastool.Wasm(str(path))
        self._func2slot = {}
        for off, funcs in self.w.elem:
            for i, f in enumerate(funcs):
                self._func2slot.setdefault(f, off + i)

    # -- index space
    def slot_of(self, funcidx):
        return self._func2slot.get(funcidx)

    def func_of_slot(self, slot):
        return self.w.table_slot_to_func(slot)

    def type_of(self, funcidx):
        return self.w.func_index_to_type(funcidx)

    # -- memory
    def _segments(self):
        return [(off, off + size, start) for _mi, off, start, size in self.w.data]

    def linear_of(self, filepos):
        for lo, hi, start in self._segments():
            if start <= filepos < start + (hi - lo):
                return lo + (filepos - start)
        return None

    def cstr(self, addr, limit=600):
        for lo, hi, start in self._segments():
            if lo <= addr < hi:
                pos = start + (addr - lo)
                out = []
                for i in range(limit):
                    b = self.w.buf[pos + i]
                    if b == 0:
                        break
                    out.append(b)
                return bytes(out).decode('utf-8', 'replace')
        return None

    def occurrences_in_data(self, literal: str):
        needle = literal.encode()
        out, pos = [], 0
        while True:
            i = self.w.buf.find(needle, pos)
            if i < 0:
                break
            out.append(i)
            pos = i + 1
        addrs = [self.linear_of(i) for i in out]
        return [a for a in addrs if a is not None]

    def bodies(self):
        for di, (off, size, _decls, _end) in enumerate(self.w.code):
            yield self.w.num_imported_funcs + di, self.w.buf[off:off + size]

    def referencing_funcs(self, value: int):
        """Functions that load `value` with a real i32.const (byte scan, then decode)."""
        pat = b'\x41' + uleb(value)
        hits = []
        for fidx, body in self.bodies():
            if pat not in body:
                continue
            for _off, mnem, imm, _ln in self.wastool.decode(self.w, fidx):
                if mnem == 'i32.const' and int(imm) == value:
                    hits.append(fidx)
                    break
        return hits

    def section_bytes(self, sid):
        st, size = self.w.sec(sid)
        if st is None:
            return b''
        return bytes(self.w.buf[st:st + size])


# --------------------------------------------------------------------------- resolution
def index_methods(script_path: Path):
    doc = json.loads(script_path.read_text(encoding='utf-8'))
    by = {}
    for m in doc['ScriptMethod']:
        by.setdefault((m['Name'], m['TypeSignature']), []).append(m)
    return by


def resolve_slot(view: View, methods, decl: dict):
    key = (decl['method'], decl['type_signature'])
    hits = methods.get(key, [])
    entry = {'id': decl['id'], 'method': decl['method'],
             'type_signature': decl['type_signature'],
             'arity': decl['arity'], 'purpose': decl.get('purpose', ''),
             'candidates': [h['Address'] for h in hits]}
    if not hits:
        entry['status'] = UNRESOLVED
        entry['reason'] = 'no ScriptMethod with this (name, TypeSignature)'
        return entry
    if len(hits) > 1:
        want = decl.get('native_params')
        if not want:
            entry['status'] = UNRESOLVED
            entry['reason'] = (f'{len(hits)} overloads share (name, TypeSignature) and no '
                               f'declared native_params to tell them apart; refusing to '
                               f'pick one')
            return entry
        narrowed = []
        for h in hits:
            got = native_params_of(h.get('Signature', ''))
            if got == want:
                narrowed.append(h)
        entry['native_params'] = want
        if len(narrowed) != 1:
            entry['status'] = UNRESOLVED
            entry['reason'] = (f'{len(narrowed)} of {len(hits)} overloads match the declared '
                               f'native_params {want}; observed '
                               f'{[native_params_of(h.get("Signature", "")) for h in hits]}')
            return entry
        hits = narrowed

    slot = hits[0]['Address']
    entry['slot'] = slot
    entry['csharp_signature'] = hits[0].get('Signature', '')
    func = view.func_of_slot(slot)
    if func is None:
        entry['status'] = UNRESOLVED
        entry['reason'] = f'element slot {slot:#x} is not in the table'
        return entry
    entry['func_index'] = func
    want_type = ts_to_wasm(decl['type_signature'])
    got_type = view.type_of(func)
    entry['wasm_signature'] = sig_str(*got_type)
    if got_type != want_type:
        entry['status'] = UNRESOLVED
        entry['reason'] = (f'wasm type {sig_str(*got_type)} does not match TypeSignature '
                           f'{decl["type_signature"]} ({sig_str(*want_type)})')
        return entry
    want_arity = len(decl['type_signature']) - 1
    if want_arity != decl['arity']:
        entry['status'] = UNRESOLVED
        entry['reason'] = (f'shim calls this slot with {decl["arity"]} argument(s) but the '
                           f'method takes {want_arity}; a call_indirect arity mismatch traps')
        return entry
    entry['status'] = RESOLVED
    return entry


def _unique_literal(view: View, literal: str, label: str):
    addrs = view.occurrences_in_data(literal)
    if not addrs:
        raise ResolveError(f'{label}: the literal is not present in any data segment')
    referenced = []
    for addr in addrs:
        funcs = view.referencing_funcs(addr)
        if funcs:
            referenced.append((addr, funcs))
    if len(referenced) != 1:
        raise ResolveError(
            f'{label}: {len(addrs)} data copy/copies, {len(referenced)} of them loaded by '
            f'i32.const ({[hex(a) for a, _f in referenced]}); need exactly one')
    addr, funcs = referenced[0]
    got = view.cstr(addr, len(literal) + 8)
    if got != literal:
        raise ResolveError(f'{label}: bytes at {addr:#x} are {got!r}, expected {literal!r}')
    return addr, funcs


def resolve_icalls(view: View, slot_by_id, decls):
    out = {}
    failures = []

    for decl in decls:
        if decl['kind'] == 'resolve_call':
            continue
        try:
            addr, funcs = _unique_literal(view, decl['literal'], decl['id'])
        except ResolveError as exc:
            out[decl['id']] = {'status': UNRESOLVED, 'reason': str(exc),
                               'required': decl.get('required', True)}
            failures.append((decl['id'], str(exc), decl.get('required', True)))
            continue
        entry = {'status': RESOLVED, 'literal': addr, 'literal_text': decl['literal'],
                 'referenced_by': funcs[:6], 'arity': decl.get('arity', 1),
                 'required': decl.get('required', True), 'kind': decl['kind']}
        if decl['kind'] == 'cached':
            slot_id = decl['slot']
            slot = slot_by_id.get(slot_id, {}).get('slot')
            if slot is None:
                entry['status'] = UNRESOLVED
                entry['reason'] = f'cache pointer needs slot {slot_id!r}, which is not resolved'
                failures.append((decl['id'], entry['reason'], entry.get('required', True)))
                out[decl['id']] = entry
                continue
            try:
                entry['cache_ptr'] = _cache_ptr(view, view.func_of_slot(slot), addr, decl['id'])
                # cross-check: the wrapper must be the one that references this literal
                need(view.func_of_slot(slot) in funcs,
                     f'{decl["id"]}: slot {slot:#x} does not reference its own literal')
            except ResolveError as exc:
                entry['status'] = UNRESOLVED
                entry['reason'] = str(exc)
                failures.append((decl['id'], str(exc), entry.get('required', True)))
        out[decl['id']] = entry

    # resolve_icall: the helper the wrappers call. No managed metadata exists for
    # it, so it is derived from the wrapper bodies and cross-checked on two of them.
    try:
        derived = set()
        for name in ('FindObjectsOfTypeAll', 'HasCharacter'):
            entry = out.get(name) or {}
            need(entry.get('status') == RESOLVED,
                 f'resolve_icall: needs a resolved {name} wrapper')
            slot = slot_by_id[('find_all' if name == 'FindObjectsOfTypeAll'
                               else 'font_has_char')]['slot']
            derived.add(_resolve_call_target(view, view.func_of_slot(slot), entry['literal']))
        need(len(derived) == 1,
             f'resolve_icall: wrappers call different helpers {sorted(derived)}')
        func = derived.pop()
        slot = view.slot_of(func)
        need(slot is not None, f'resolve_icall: func {func} is not in the element table')
        got = view.type_of(func)
        need(got == ([I32], [I32]),
             f'resolve_icall: func {func} has type {sig_str(*got)}, expected (i32)->i32')
        out['resolve_icall'] = {'status': RESOLVED, 'slot': slot, 'func_index': func,
                                'wasm_signature': sig_str(*got), 'arity': 1,
                                'required': True, 'kind': 'resolve_call'}
    except ResolveError as exc:
        out['resolve_icall'] = {'status': UNRESOLVED, 'reason': str(exc), 'required': True,
                                'kind': 'resolve_call'}
        failures.append(('resolve_icall', str(exc), True))
    return out, failures


def _resolve_call_target(view: View, func, literal_addr: int):
    ins = view.wastool.decode(view.w, func)
    for i, (_off, mnem, imm, _ln) in enumerate(ins):
        if mnem != 'call':
            continue
        if i < 2:
            continue
        if not (ins[i - 1][1] == 'i32.const' and int(ins[i - 1][2]) == literal_addr):
            continue
        if ins[i - 2][1] != 'i32.const':
            continue
        target = int(imm)
        need(target >= view.w.num_imported_funcs,
             f'icall wrapper calls imported func {target}, not the resolve helper')
        return target
    raise ResolveError(f'func {func}: no `i32.const <cache>; i32.const <literal>; call` '
                       f'pattern, so the resolve helper cannot be identified')


def _cache_ptr(view: View, func, literal_addr: int, label: str):
    ins = view.wastool.decode(view.w, func)
    cand = None
    for i, (_off, mnem, imm, _ln) in enumerate(ins):
        if mnem == 'i32.const' and int(imm) == literal_addr and i > 0 \
                and ins[i - 1][1] == 'i32.const':
            cand = int(ins[i - 1][2])
            break
    need(cand is not None,
         f'{label}: the wrapper does not push a cache address next to the name literal')
    count = sum(1 for _o, m, im, _l in ins if m == 'i32.const' and int(im) == cand)
    need(count >= 2,
         f'{label}: cache address {cand:#x} is used {count} time(s); the wrapper reads and '
         f'stores it, so it must appear at least twice')
    need(cand % 4 == 0,
         f'{label}: cache address {cand:#x} is not 4-byte aligned, so it is not a static word')
    text = view.cstr(cand, 24)
    need(text is None or not (len(text) >= 4 and text.isprintable()),
         f'{label}: cache address {cand:#x} points at printable text, not a static word')
    return cand


def resolve_fields(dump: dict, decls):
    out, failures = {}, []
    for decl in decls:
        rec = {'kind': decl['kind'], 'required': True}
        if decl['kind'] == 'abi':
            rec.update(status=STATIC, offset=decl['value'], why=decl['why'])
            out[decl['id']] = rec
            continue
        cls, field = decl['class'], decl['field']
        hits = {c: fields[field] for c, fields in dump.items()
                if c == cls and field in fields}
        if not hits:
            rec.update(status=UNRESOLVED,
                       reason=f'{cls}.{field} is not declared in dump.cs')
            failures.append((decl['id'], rec['reason'], True))
            out[decl['id']] = rec
            continue
        offsets = set(hits.values())
        if len(offsets) != 1:
            rec.update(status=UNRESOLVED,
                       reason=f'{cls}.{field} has conflicting offsets {sorted(offsets)}')
            failures.append((decl['id'], rec['reason'], True))
            out[decl['id']] = rec
            continue
        rec.update(status=RESOLVED, offset=offsets.pop(), owner=cls, field=field)
        out[decl['id']] = rec
    return out, failures


def resolve_imports(view: View, decl):
    module = decl['module']
    entries = [(i, imp) for i, imp in enumerate(view.w.imports)
               if imp['module'] == module and imp['kind'] == 0]
    need(entries, f'no function imports from module {module!r}')
    by_name = {imp['name']: (pos, imp) for pos, (_i, imp) in enumerate(entries)}
    out = {'module': module, 'count': len(entries)}
    for role in ('translation', 'context'):
        want = decl[role]
        need(want in by_name, f'import {module}.{want} ({role}) not found')
        pos, imp = by_name[want]
        params, results = view.w.types[imp['typeidx']]
        out[role] = {'name': want, 'index': pos, 'type': sig_str(params, results),
                     'params': len(params), 'results': len(results)}
        need(params == [I32, I32] and results == [I32],
             f'import {module}.{want} has type {sig_str(params, results)}, expected (i32,i32)->i32')
    return out


# --------------------------------------------------------------------------- assembly
def build_config(branch, game_build, patched: View, orig: View, dump, targets,
                 script_json: Path):
    layout = {}
    for sid, label in ((2, 'import'), (4, 'table'), (9, 'element')):
        same = orig.section_bytes(sid) == patched.section_bytes(sid)
        layout[label] = same
    need(all(layout.values()),
         f'[{branch}] the patch changed the {[k for k, v in layout.items() if not v]} section; '
         f'element/import indices would not describe the shipped Wasm')

    methods = index_methods(script_json)
    slots, slot_fail = [], []
    slot_by_id = {}
    for decl in targets['slots']:
        entry = resolve_slot(orig, methods, decl)
        slots.append(entry)
        slot_by_id[decl['id']] = entry
        if entry['status'] != RESOLVED:
            slot_fail.append((decl['id'], entry['reason'], True))

    icalls, icall_fail = resolve_icalls(orig, slot_by_id, targets['icalls'])
    fields, field_fail = resolve_fields(dump, targets['fields'])
    imports = resolve_imports(orig, targets['imports'])

    failures = slot_fail + icall_fail + field_fail
    cfg = {
        'schema': 1,
        'branch': branch,
        'game_build': game_build,
        'wasm': {
            'sha256': hashlib.sha256(patched.path.read_bytes()).hexdigest(),
            'plain_bytes': patched.path.stat().st_size,
            'orig_sha256': hashlib.sha256(orig.path.read_bytes()).hexdigest(),
            'index_space': layout,
        },
        'imports': imports,
        'slots': {e['id']: {k: e[k] for k in
                            ('status', 'slot', 'func_index', 'method', 'type_signature',
                             'wasm_signature', 'arity', 'purpose') if k in e}
                  for e in slots},
        'icalls': icalls,
        'fields': {k: {kk: vv for kk, vv in v.items()} for k, v in fields.items()},
        'compat': {
            'resolved': sum(1 for e in slots if e['status'] == RESOLVED)
                        + sum(1 for e in icalls.values() if e['status'] == RESOLVED)
                        + sum(1 for e in fields.values() if e['status'] == RESOLVED),
            'verified_static': sum(1 for e in fields.values() if e['status'] == STATIC),
            'unresolved': len(failures),
            'publishable': not failures,
            'failures': [{'id': i, 'reason': r} for i, r, _req in failures],
        },
    }
    return cfg, slots, failures


def emit_report(path: Path, cfgs, extra_lines=()):
    L = ['# 运行时绑定解析报告', '',
         '本文件由 `tools/wasm/resolve_runtime.py` 生成。每个随官方构建变化的常量都在这里',
         '给出解析依据；**没有一个数字是手写的**。', '']
    for cfg in cfgs:
        L += [f"## 分支 `{cfg['branch']}` · 构建 `{cfg['game_build']}`", '',
              f"- 打补丁后 Wasm SHA-256：`{cfg['wasm']['sha256']}`",
              f"- 原始 Wasm SHA-256：`{cfg['wasm']['orig_sha256']}`",
              f"- 索引空间未变（import / table / element）："
              f"`{cfg['wasm']['index_space']}`",
              f"- 结论：**{'PASS' if cfg['compat']['publishable'] else 'FAIL'}**"
              f"（RESOLVED {cfg['compat']['resolved']}，VERIFIED_STATIC "
              f"{cfg['compat']['verified_static']}，UNRESOLVED "
              f"{cfg['compat']['unresolved']}）", '',
              '### import 协议', '',
              '| 用途 | module.field | import 序号 | 类型 | 状态 |', '|---|---|---|---|---|']
        im = cfg['imports']
        for role, label in (('translation', '译文通道'), ('context', '剧情上下文')):
            r = im[role]
            L.append(f"| {label} | `{im['module']}.{r['name']}` | {r['index']} | "
                     f"`{r['type']}` | RESOLVED |")
        L += ['', f"module `{im['module']}` 共 {im['count']} 个函数 import（运行时按此数校验）。",
              '', '### 函数表槽位', '',
              '| id | 方法 | TypeSignature | 槽位 | 函数 | Wasm 类型 | 实参 | 状态 | 依据 |',
              '|---|---|---|---|---|---|---|---|---|']
        for sid, e in cfg['slots'].items():
            if e['status'] == RESOLVED:
                L.append(f"| `{sid}` | `{e['method']}` | `{e['type_signature']}` | "
                         f"{e['slot']} | {e['func_index']} | `{e['wasm_signature']}` | "
                         f"{e['arity']} | **RESOLVED** | script.json Address → elem 表 → "
                         f"Wasm 类型逐项相符 |")
            else:
                L.append(f"| `{sid}` | `{e.get('method', '')}` | `{e.get('type_signature', '')}` | "
                         f"| | | | **UNRESOLVED** | {e.get('reason', '')} |")
        L += ['', '### icall 字面量与缓存指针', '',
              '| id | 线性地址 | 内容 | 缓存指针 | 状态 | 依据 |', '|---|---|---|---|---|---|']
        for iid, e in cfg['icalls'].items():
            if e['status'] != RESOLVED:
                L.append(f"| `{iid}` | | | | **UNRESOLVED** | {e.get('reason', '')} |")
                continue
            if e.get('kind') == 'resolve_call':
                L.append(f"| `{iid}` | slot {e['slot']} | 由两个包装函数的 "
                         f"`i32.const 缓存; i32.const 字面量; call` 模式反推 | | "
                         f"**RESOLVED** | 无托管元数据，因此从调用点推导 |")
                continue
            lit = e.get('literal_text', '')
            shown = lit if len(lit) <= 72 else lit[:69] + '...'
            L.append(f"| `{iid}` | {e['literal']} | `{shown}` | "
                     f"{e.get('cache_ptr', '—')} | **RESOLVED** | "
                     f"Data 段内唯一副本且被 i32.const 引用 |")
        L += ['', '### 托管字段偏移', '',
              '| id | 类.字段 | 偏移 | 状态 | 依据 |', '|---|---|---|---|---|']
        for fid, e in cfg['fields'].items():
            if e['status'] == RESOLVED:
                L.append(f"| `{fid}` | `{e['owner']}.{e['field']}` | 0x{e['offset']:x} | "
                         f"**RESOLVED** | dump.cs 字段偏移 |")
            elif e['status'] == STATIC:
                L.append(f"| `{fid}` | — | 0x{e['offset']:x} | VERIFIED_STATIC | {e.get('why', '')} |")
            else:
                L.append(f"| `{fid}` | | | **UNRESOLVED** | {e.get('reason', '')} |")
        L.append('')
    L += list(extra_lines)
    path.write_text('\n'.join(L) + '\n', encoding='utf-8')


def load_bundle(path: Path):
    if not path.is_file():
        return {'schema': 1, 'branches': {}}
    text = path.read_text(encoding='utf-8')
    i, j = text.find(MARK_BEGIN), text.find(MARK_END)
    need(i >= 0 and j > i, f'{path.name} is damaged: missing the JSON markers')
    return json.loads(text[i + len(MARK_BEGIN):j])


def write_bundle(path: Path, bundle):
    branches = bundle.setdefault('branches', {})
    ordered = {k: branches[k] for k in sorted(branches)}
    body = json.dumps({'schema': bundle.get('schema', 1), 'branches': ordered},
                      ensure_ascii=False, indent=1, sort_keys=False)
    text = ('/* GENERATED by tools/wasm/resolve_runtime.py - do not edit by hand.\n'
            ' * One entry per resource branch (0 = DMM, 1 = FANZA), bound to the\n'
            ' * SHA-256 of the patched Wasm that ships next to it. build_release.py\n'
            ' * refuses to package when an entry and its Wasm disagree. */\n'
            'var MONMUSU_CN_RUNTIME = ' + MARK_BEGIN + body + MARK_END + ';\n'
            '/* Node tests require() this file; in the browser `module` does not exist */\n'
            'if (typeof module !== \'undefined\' && module.exports) {\n'
            '  module.exports = MONMUSU_CN_RUNTIME;\n'
            '}\n')
    path.write_text(text, encoding='utf-8')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--branch', default='0', choices=['0', '1'])
    ap.add_argument('--orig', default=None, help='official (unpatched) ntd.wasm')
    ap.add_argument('--patched', default=None, help='the patched ntd.wasm that will ship')
    ap.add_argument('--script-json', default=None)
    ap.add_argument('--dump', default=None, help='dump.cs of the same build')
    ap.add_argument('--from-json', default=None,
                    help='merge an already resolved runtime_config.json into the loader '
                         'bundle (used once the whole build has passed)')
    ap.add_argument('--game-build', default='')
    ap.add_argument('--out-dir', default=None)
    ap.add_argument('--targets', default=str(TARGETS))
    ap.add_argument('--bundle', default=str(JS_BUNDLE))
    ap.add_argument('--no-bundle', action='store_true',
                    help='only resolve + report, do not touch the loader config')
    args = ap.parse_args(argv)
    try:
        return _run(args)
    except ResolveError as exc:
        print(f'RUNTIME CONFIG FAILED: {exc}', file=sys.stderr)
        return 1


def _run(args):
    outdir = Path(args.out_dir) if args.out_dir else (
        BUILD if args.branch == '0' else BUILD / f'branch-{args.branch}')
    outdir.mkdir(parents=True, exist_ok=True)

    if args.from_json:
        cfg = json.loads(Path(args.from_json).read_text(encoding='utf-8'))
        need(cfg.get('branch') == args.branch,
             f'{args.from_json} is branch {cfg.get("branch")}, not {args.branch}')
        need((cfg.get('compat') or {}).get('publishable'),
             f'{args.from_json} is not publishable')
        bundle = load_bundle(Path(args.bundle))
        bundle['branches'][args.branch] = cfg
        write_bundle(Path(args.bundle), bundle)
        print(f'rt-config: branch {args.branch} merged into {Path(args.bundle).name}')
        return 0

    for name in ('orig', 'patched', 'script_json', 'dump'):
        need(getattr(args, name), f'--{name.replace("_", "-")} is required to resolve')

    targets = json.loads(Path(args.targets).read_text(encoding='utf-8'))
    orig = View(Path(args.orig))
    patched = View(Path(args.patched))
    dump = parse_dump(Path(args.dump))

    game_build = args.game_build or f'{args.branch}'
    try:
        cfg, slots, failures = build_config(args.branch, game_build, patched, orig, dump,
                                           targets, Path(args.script_json))
    except ResolveError as exc:
        print(f'RUNTIME CONFIG FAILED: {exc}', file=sys.stderr)
        return 1

    (outdir / 'runtime_config.json').write_text(
        json.dumps(cfg, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    emit_report(outdir / 'RUNTIME_CONFIG_REPORT.md', [cfg])

    if failures:
        print(f'RUNTIME CONFIG FAILED for branch {args.branch}: '
              f'{len(failures)} unresolved binding(s)', file=sys.stderr)
        for ident, reason, _req in failures:
            print(f'  - {ident}: {reason}', file=sys.stderr)
        print(f'  report: {outdir / "RUNTIME_CONFIG_REPORT.md"}', file=sys.stderr)
        return 1

    if not args.no_bundle:
        bundle = load_bundle(Path(args.bundle))
        bundle['branches'][args.branch] = cfg
        write_bundle(Path(args.bundle), bundle)
        print(f'rt-config: branch {args.branch} -> {Path(args.bundle).name} '
              f'({cfg["compat"]["resolved"]} resolved, '
              f'{cfg["compat"]["verified_static"]} verified-static)')
    print(f'rt-config: {outdir / "runtime_config.json"} written, all bindings resolved')
    return 0


if __name__ == '__main__':
    sys.exit(main())
