"""Minimal but exact WebAssembly parser for Unity IL2CPP WebGL builds.

Provides: section table, type/import/function/table/export/elem/code/data access,
per-function body bytes, and a full instruction decoder (enough for patching).
"""
import struct, sys, json

class Wasm:
    def __init__(self, path):
        self.path = path
        self.buf = open(path, 'rb').read()
        assert self.buf[:4] == b'\x00asm', 'not a wasm file'
        self.sections = []   # (id, payload_start, payload_size)
        self._parse_sections()
        self.types = []          # list of (params:[vt], results:[vt])
        self.imports = []        # list of dict(kind, module, name, typeidx/...)
        self.func_types = []     # typeidx per defined function
        self.tables = []         # (elemtype, min, max)
        self.exports = []        # (name, kind, index)
        self.elem = []           # list of (offset, [funcidx...])
        self.code = []           # list of (body_start, body_size, local_decls)
        self.data = []           # list of (memidx, offset, start, size)
        self._parse_all()

    # ---------- primitives ----------
    def uleb(self, p):
        r = 0; s = 0; b = self.buf
        while True:
            x = b[p]; p += 1
            r |= (x & 0x7f) << s
            if not (x & 0x80): return r, p
            s += 7

    def sleb(self, p):
        r = 0; s = 0; b = self.buf
        while True:
            x = b[p]; p += 1
            r |= (x & 0x7f) << s
            s += 7
            if not (x & 0x80):
                if s < 64 and (x & 0x40): r |= -(1 << s)
                return r, p

    def _parse_sections(self):
        p = 8
        while p < len(self.buf):
            sid = self.buf[p]; p += 1
            size, p = self.uleb(p)
            self.sections.append((sid, p, size))
            p += size

    def sec(self, sid):
        for s, start, size in self.sections:
            if s == sid: return start, size
        return None, 0

    # ---------- sections ----------
    def _parse_all(self):
        # type
        st, sz = self.sec(1)
        if st:
            p = st; n, p = self.uleb(p)
            for _ in range(n):
                assert self.buf[p] == 0x60, 'only functype supported'
                p += 1
                np_, p = self.uleb(p); params = list(self.buf[p:p+np_]); p += np_
                nr, p = self.uleb(p); results = list(self.buf[p:p+nr]); p += nr
                self.types.append((params, results))
        # import
        st, sz = self.sec(2)
        if st:
            p = st; n, p = self.uleb(p)
            for _ in range(n):
                ml, p = self.uleb(p); mod = self.buf[p:p+ml].decode(); p += ml
                nl, p = self.uleb(p); nm = self.buf[p:p+nl].decode(); p += nl
                kind = self.buf[p]; p += 1
                d = dict(module=mod, name=nm, kind=kind)
                if kind == 0:
                    ti, p = self.uleb(p); d['typeidx'] = ti
                elif kind == 1:
                    d['elemtype'] = self.buf[p]; p += 1
                    fl = self.buf[p]; p += 1
                    mn, p = self.uleb(p); d['min'] = mn
                    if fl & 1:
                        mx, p = self.uleb(p); d['max'] = mx
                elif kind == 2:
                    fl = self.buf[p]; p += 1
                    mn, p = self.uleb(p); d['min'] = mn
                    if fl & 1:
                        mx, p = self.uleb(p); d['max'] = mx
                elif kind == 3:
                    d['valtype'] = self.buf[p]; p += 1; d['mut'] = self.buf[p]; p += 1
                self.imports.append(d)
        self.num_imported_funcs = sum(1 for i in self.imports if i['kind'] == 0)
        # function
        st, sz = self.sec(3)
        if st:
            p = st; n, p = self.uleb(p)
            for _ in range(n):
                ti, p = self.uleb(p); self.func_types.append(ti)
        # table
        st, sz = self.sec(4)
        if st:
            p = st; n, p = self.uleb(p)
            for _ in range(n):
                et = self.buf[p]; p += 1
                fl = self.buf[p]; p += 1
                mn, p = self.uleb(p); mx = None
                if fl & 1: mx, p = self.uleb(p)
                self.tables.append((et, mn, mx))
        # export
        st, sz = self.sec(7)
        if st:
            p = st; n, p = self.uleb(p)
            for _ in range(n):
                nl, p = self.uleb(p); nm = self.buf[p:p+nl].decode(); p += nl
                kind = self.buf[p]; p += 1
                idx, p = self.uleb(p)
                self.exports.append((nm, kind, idx))
        # elem
        st, sz = self.sec(9)
        if st:
            p = st; n, p = self.uleb(p)
            for _ in range(n):
                flags, p = self.uleb(p)
                if flags == 0:
                    off, p = self._read_init_expr(p)
                    cnt, p = self.uleb(p)
                    funcs = []
                    for _ in range(cnt):
                        fi, p = self.uleb(p); funcs.append(fi)
                    self.elem.append((off, funcs))
                else:
                    raise NotImplementedError(f'elem flags {flags}')
        # code
        st, sz = self.sec(10)
        if st:
            p = st; n, p = self.uleb(p)
            for _ in range(n):
                bsize, p = self.uleb(p)
                body = p
                q = p
                nl, q = self.uleb(q)
                decls = []
                for _ in range(nl):
                    cnt, q = self.uleb(q)
                    vt = self.buf[q]; q += 1
                    decls.append((cnt, vt))
                # strip trailing 'end'
                self.code.append((body, bsize, decls, q))
                p = body + bsize
        # data
        st, sz = self.sec(11)
        if st:
            p = st; n, p = self.uleb(p)
            for _ in range(n):
                flags, p = self.uleb(p)
                if flags == 0:
                    off, p = self._read_init_expr(p)
                    size, p = self.uleb(p)
                    self.data.append((0, off, p, size))
                    p += size
                elif flags == 2:
                    mi, p = self.uleb(p)
                    off, p = self._read_init_expr(p)
                    size, p = self.uleb(p)
                    self.data.append((mi, off, p, size))
                    p += size
                else:
                    raise NotImplementedError(f'data flags {flags}')

    def _read_init_expr(self, p):
        # supports i32.const / global.get followed by end
        op = self.buf[p]; p += 1
        if op == 0x41:
            v, p = self.sleb(p)
        elif op == 0x23:
            v, p = self.uleb(p)
        else:
            raise NotImplementedError(f'init expr op {op:#x}')
        assert self.buf[p] == 0x0b, 'expected end'
        p += 1
        return v, p

    # ---------- helpers ----------
    def func_index_to_type(self, fidx):
        """fidx in the unified index space (imports first)."""
        if fidx < self.num_imported_funcs:
            k = 0
            for imp in self.imports:
                if imp['kind'] == 0:
                    if k == fidx: return self.types[imp['typeidx']]
                    k += 1
        return self.types[self.func_types[fidx - self.num_imported_funcs]]

    def table_slot_to_func(self, slot):
        for off, funcs in self.elem:
            if off <= slot < off + len(funcs):
                return funcs[slot - off]
        return None

    def func_body(self, fidx):
        return self.code[fidx - self.num_imported_funcs]

    def export_func(self, name):
        for nm, kind, idx in self.exports:
            if nm == name and kind == 0: return idx
        return None


# ---------------- instruction decoder ----------------
VALTYPE = {0x7f: 'i32', 0x7e: 'i64', 0x7d: 'f32', 0x7c: 'f64',
           0x7b: 'v128', 0x70: 'funcref', 0x6f: 'externref'}

# opcode -> (mnemonic, immediate_kind)
# immediate kinds: none, i32, i64, f32, f64, br, br_table, call, call_indirect,
#                  memarg, local, global, table, reftype, block, select_t, heaptype, mem
SIMPLE = {}
def _s(code, name, imm='none'):
    SIMPLE[code] = (name, imm)

_s(0x00, 'unreachable'); _s(0x01, 'nop'); _s(0x02, 'block', 'block'); _s(0x03, 'loop', 'block')
_s(0x04, 'if', 'block'); _s(0x05, 'else'); _s(0x0b, 'end'); _s(0x0c, 'br', 'br')
_s(0x0d, 'br_if', 'br'); _s(0x0e, 'br_table', 'br_table'); _s(0x0f, 'return')
_s(0x10, 'call', 'call'); _s(0x11, 'call_indirect', 'call_indirect')
_s(0x1a, 'drop'); _s(0x1b, 'select'); _s(0x1c, 'select_t', 'select_t')
_s(0x20, 'local.get', 'local'); _s(0x21, 'local.set', 'local'); _s(0x22, 'local.tee', 'local')
_s(0x23, 'global.get', 'global'); _s(0x24, 'global.set', 'global')
_s(0x25, 'table.get', 'table'); _s(0x26, 'table.set', 'table')
_s(0x28, 'i32.load', 'memarg'); _s(0x29, 'i64.load', 'memarg'); _s(0x2a, 'f32.load', 'memarg')
_s(0x2b, 'f64.load', 'memarg'); _s(0x2c, 'i32.load8_s', 'memarg'); _s(0x2d, 'i32.load8_u', 'memarg')
_s(0x2e, 'i32.load16_s', 'memarg'); _s(0x2f, 'i32.load16_u', 'memarg')
_s(0x30, 'i64.load8_s', 'memarg'); _s(0x31, 'i64.load8_u', 'memarg')
_s(0x32, 'i64.load16_s', 'memarg'); _s(0x33, 'i64.load16_u', 'memarg')
_s(0x34, 'i64.load32_s', 'memarg'); _s(0x35, 'i64.load32_u', 'memarg')
_s(0x36, 'i32.store', 'memarg'); _s(0x37, 'i64.store', 'memarg')
_s(0x38, 'f32.store', 'memarg'); _s(0x39, 'f64.store', 'memarg')
_s(0x3a, 'i32.store8', 'memarg'); _s(0x3b, 'i32.store16', 'memarg')
_s(0x3c, 'i64.store8', 'memarg'); _s(0x3d, 'i64.store16', 'memarg'); _s(0x3e, 'i64.store32', 'memarg')
_s(0x3f, 'memory.size', 'mem'); _s(0x40, 'memory.grow', 'mem')
_s(0x41, 'i32.const', 'i32'); _s(0x42, 'i64.const', 'i64')
_s(0x43, 'f32.const', 'f32'); _s(0x44, 'f64.const', 'f64')
for i, nm in enumerate(['i32.eqz', 'i32.eq', 'i32.ne', 'i32.lt_s', 'i32.lt_u', 'i32.gt_s',
                        'i32.gt_u', 'i32.le_s', 'i32.le_u', 'i32.ge_s', 'i32.ge_u']):
    _s(0x45 + i, nm)
for i, nm in enumerate(['i64.eqz', 'i64.eq', 'i64.ne', 'i64.lt_s', 'i64.lt_u', 'i64.gt_s',
                        'i64.gt_u', 'i64.le_s', 'i64.le_u', 'i64.ge_s', 'i64.ge_u']):
    _s(0x50 + i, nm)
for i, nm in enumerate(['f32.eq', 'f32.ne', 'f32.lt', 'f32.gt', 'f32.le', 'f32.ge']):
    _s(0x5b + i, nm)
for i, nm in enumerate(['f64.eq', 'f64.ne', 'f64.lt', 'f64.gt', 'f64.le', 'f64.ge']):
    _s(0x61 + i, nm)
for i, nm in enumerate(['i32.clz', 'i32.ctz', 'i32.popcnt', 'i32.add', 'i32.sub', 'i32.mul',
                        'i32.div_s', 'i32.div_u', 'i32.rem_s', 'i32.rem_u', 'i32.and',
                        'i32.or', 'i32.xor', 'i32.shl', 'i32.shr_s', 'i32.shr_u',
                        'i32.rotl', 'i32.rotr']):
    _s(0x67 + i, nm)
for i, nm in enumerate(['i64.clz', 'i64.ctz', 'i64.popcnt', 'i64.add', 'i64.sub', 'i64.mul',
                        'i64.div_s', 'i64.div_u', 'i64.rem_s', 'i64.rem_u', 'i64.and',
                        'i64.or', 'i64.xor', 'i64.shl', 'i64.shr_s', 'i64.shr_u',
                        'i64.rotl', 'i64.rotr']):
    _s(0x79 + i, nm)
for i, nm in enumerate(['f32.abs', 'f32.neg', 'f32.ceil', 'f32.floor', 'f32.trunc',
                        'f32.nearest', 'f32.sqrt', 'f32.add', 'f32.sub', 'f32.mul',
                        'f32.div', 'f32.min', 'f32.max', 'f32.copysign']):
    _s(0x8b + i, nm)
for i, nm in enumerate(['f64.abs', 'f64.neg', 'f64.ceil', 'f64.floor', 'f64.trunc',
                        'f64.nearest', 'f64.sqrt', 'f64.add', 'f64.sub', 'f64.mul',
                        'f64.div', 'f64.min', 'f64.max', 'f64.copysign']):
    _s(0x99 + i, nm)
for i, nm in enumerate(['i32.wrap_i64', 'i32.trunc_f32_s', 'i32.trunc_f32_u',
                        'i32.trunc_f64_s', 'i32.trunc_f64_u', 'i64.extend_i32_s',
                        'i64.extend_i32_u', 'i64.trunc_f32_s', 'i64.trunc_f32_u',
                        'i64.trunc_f64_s', 'i64.trunc_f64_u', 'f32.convert_i32_s',
                        'f32.convert_i32_u', 'f32.convert_i64_s', 'f32.convert_i64_u',
                        'f32.demote_f64', 'f64.convert_i32_s', 'f64.convert_i32_u',
                        'f64.convert_i64_s', 'f64.convert_i64_u', 'f64.promote_f32',
                        'i32.reinterpret_f32', 'i64.reinterpret_f64', 'f32.reinterpret_i32',
                        'f64.reinterpret_i64']):
    _s(0xa7 + i, nm)


def decode(w, fidx, limit=None, start_off=None):
    """Decode a function body into a list of (offset, mnemonic, immediate_text, length)."""
    body_start, body_size, decls, p = w.func_body(fidx)
    end = body_start + body_size
    if start_off is not None: p = start_off
    out = []
    depth = 0
    while p < end:
        off = p
        op = w.buf[p]; p += 1
        if op == 0xfc:
            sub, p = w.uleb(p)
            names = {0: 'i32.trunc_sat_f32_s', 1: 'i32.trunc_sat_f32_u',
                     2: 'i32.trunc_sat_f64_s', 3: 'i32.trunc_sat_f64_u'}
            out.append((off, f'0xfc/{sub}', '', p - off))
            continue
        if op not in SIMPLE:
            out.append((off, f'??0x{op:02x}', '', p - off)); continue
        name, imm = SIMPLE[op]
        txt = ''
        if imm == 'i32':
            v, p = w.sleb(p); txt = str(v)
        elif imm == 'i64':
            v, p = w.sleb(p); txt = str(v)
        elif imm == 'f32':
            p += 4
        elif imm == 'f64':
            p += 8
        elif imm in ('br', 'local', 'global', 'call', 'table', 'mem'):
            v, p = w.uleb(p); txt = str(v)
        elif imm == 'br_table':
            n, p = w.uleb(p)
            vs = []
            for _ in range(n + 1):
                v, p = w.uleb(p); vs.append(v)
            txt = ','.join(map(str, vs))
        elif imm == 'call_indirect':
            ti, p = w.uleb(p); tb, p = w.uleb(p); txt = f'type={ti} table={tb}'
        elif imm == 'block':
            bt = w.buf[p]; p += 1
            txt = VALTYPE.get(bt, str(bt))
        elif imm == 'select_t':
            n, p = w.uleb(p); p += n
        elif imm == 'memarg':
            a, p = w.uleb(p); o, p = w.uleb(p); txt = f'off={o} align={a}'
        elif imm == 'mem':
            p += 1
        out.append((off, name, txt, p - off))
        if limit and len(out) >= limit: break
    return out


if __name__ == '__main__':
    w = Wasm(sys.argv[1])
    print('types', len(w.types), 'imports', len(w.imports),
          'imported funcs', w.num_imported_funcs, 'defined funcs', len(w.func_types))
    print('tables', w.tables)
    print('elem segments', len(w.elem), 'total slots', sum(len(f) for _, f in w.elem))
    print('elem base offsets', [o for o, _ in w.elem][:10])
    print('data segments', len(w.data))
    print('exports', len(w.exports))
