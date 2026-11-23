"""Disassemble wasm functions of the WebGL IL2CPP build with IL2CPP symbols resolved."""
import sys, json, os
import wastool

class Ctx:
    def __init__(self, wasm_path=None, namemap=None):
        here = os.path.dirname(os.path.abspath(__file__))
        wasm_path = wasm_path or os.path.join(here, 'inputs', 'ntd.orig.wasm')
        namemap = namemap or os.path.join(here, 'inputs', 'slot2name.json')
        self.w = wastool.Wasm(wasm_path)
        self.slot2name = {int(k): v for k, v in json.load(open(namemap)).items()}
        # global func index -> slot (first slot that references it)
        self.func2slot = {}
        for off, funcs in self.w.elem:
            for i, f in enumerate(funcs):
                self.func2slot.setdefault(f, off + i)
        self.slot2func = {}
        for off, funcs in self.w.elem:
            for i, f in enumerate(funcs):
                self.slot2func[off + i] = f

    def name_of_slot(self, s):
        return self.slot2name.get(s)

    def name_of_func(self, fidx):
        s = self.func2slot.get(fidx)
        if s is None: return None
        return self.slot2name.get(s)

    def func_for_slot(self, s):
        return self.slot2func.get(s)

    def disasm(self, slot, maxn=400, annotate=True):
        f = self.func_for_slot(slot)
        if f is None:
            print(f'slot {slot:#x} not in table'); return []
        lines = wastool.decode(self.w, f, limit=maxn)
        out = []
        for off, mn, imm, ln in lines:
            extra = ''
            if mn == 'call':
                nm = self.name_of_func(int(imm))
                if nm: extra = f'  ; {nm}'
                else: extra = '  ; <anon>'
            elif mn == 'call_indirect':
                # find the slot nearby
                extra = '  ; indirect'
            elif mn == 'i32.const' and annotate:
                try:
                    v = int(imm)
                    if 0x400 <= v < 0x700000:
                        extra = f'  ; mem {v:#x}'
                except Exception: pass
            out.append((off, f'{mn:<14}{imm}{extra}'))
        return out


def main():
    slot = int(sys.argv[1], 0)
    maxn = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    c = Ctx()
    f = c.func_for_slot(slot)
    p, r = c.w.func_index_to_type(f)
    print(f'slot {slot:#x} -> global func {f} (defined index {f - c.w.num_imported_funcs})')
    print(f'name: {c.name_of_slot(slot)}')
    print(f'type: ({",".join(wastool.VALTYPE[v] for v in p)}) -> ({",".join(wastool.VALTYPE[v] for v in r)})')
    body_start, body_size, decls, _ = c.w.func_body(f)
    print(f'body: off {body_start:#x} size {body_size} locals {decls}')
    print('-' * 60)
    for off, txt in c.disasm(slot, maxn):
        print(f'{off:08x}  {txt}')


if __name__ == '__main__':
    main()
