"""Find call/table xrefs inside the wasm code section."""
import sys, json
import wastool
from ildis import Ctx


def scan_calls(ctx, target_global_idx):
    hits = []
    for di, (body, size, decls, ds) in enumerate(ctx.w.code):
        gi = di + ctx.w.num_imported_funcs
        for off, mn, imm, ln in wastool.decode(ctx.w, gi):
            if mn == 'call' and int(imm) == target_global_idx:
                hits.append(gi)
                break
    return hits


def scan_static_access(ctx, klassaddr, fieldoff):
    """find functions that load *( *[klass]+0x5c ) + fieldoff"""
    hits = []
    for di, (body, size, decls, ds) in enumerate(ctx.w.code):
        gi = di + ctx.w.num_imported_funcs
        ins = wastool.decode(ctx.w, gi)
        for i in range(len(ins) - 2):
            if ins[i][1] == 'i32.const' and int(ins[i][2]) == klassaddr:
                if ins[i+1][1] == 'i32.load' and ins[i+2][1] == 'i32.load':
                    hits.append(gi)
                    break
    return hits


if __name__ == '__main__':
    ctx = Ctx()
    if sys.argv[1] == 'calls':
        slot = int(sys.argv[2], 0)
        f = ctx.func_for_slot(slot)
        gi = f + ctx.w.num_imported_funcs
        hits = scan_calls(ctx, gi)
        print(f'slot {slot:#x} (func {f}) called from {len(hits)} sites:')
        for h in hits[:80]:
            s = ctx.func2slot.get(h)
            print(f'  func {h}  slot {hex(s) if s else None}  {ctx.name_of_slot(s) if s else "<not in table>"}')
    elif sys.argv[1] == 'static':
        klass = int(sys.argv[2], 0)
        hits = scan_static_access(ctx, klass, 0)
        print(f'functions dereferencing klass {klass:#x} staticfields: {len(hits)}')
        for h in hits[:60]:
            s = ctx.func2slot.get(h)
            print(f'  func {h}  slot {hex(s) if s else "?"}  {ctx.name_of_slot(s) if s else "?"}')
