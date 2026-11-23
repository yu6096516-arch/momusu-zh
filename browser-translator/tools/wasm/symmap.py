"""Build slot -> method name map from Il2CppDumper dump.cs, and disassemble wasm functions
with symbolic call/global resolution.
"""
import re, json, sys, os
import wastool

HERE = os.path.dirname(os.path.abspath(__file__))
DUMP = os.path.join(HERE, 'inputs', 'dump.cs')

def build_map(dump_path=DUMP):
    ns = ''; cls = ''; slot2name = {}
    rva_re = re.compile(r'// RVA: 0x([0-9A-Fa-f]+)')
    pending = None
    for line in open(dump_path, encoding='utf-8', errors='replace'):
        s = line.strip()
        if s.startswith('// Namespace:'):
            ns = s.split(':', 1)[1].strip()
            continue
        m = re.match(r'^(?:public|private|protected|internal|static|sealed|abstract|partial|readonly|class|interface|struct|enum|\[).*(class|struct|enum|interface)\s+([\w`.<>]+)', s)
        if rva_re.search(s):
            pending = int(rva_re.search(s).group(1), 16)
            continue
        if pending is not None and s and not s.startswith('//'):
            mname = s.split('(')[0].split('{')[0].strip()
            mname = mname.split()[-1] if mname else '?'
            slot2name.setdefault(pending, f'{ns}.{cls}::{mname}' if cls else mname)
            pending = None
        mm = re.match(r'^public (?:sealed |abstract |static )*(?:class|struct|interface|enum) (\w+)', s)
        if mm:
            cls = mm.group(1)
    return slot2name

if __name__ == '__main__':
    m = build_map()
    print('slots mapped:', len(m))
    out = os.path.join(HERE, 'inputs', 'slot2name.json')
    json.dump(m, open(out, 'w'), ensure_ascii=False)
    print('wrote', out)
    for k in [0xB8DB, 0xB8DC, 0xB8DD, 0xB8DE, 0x19557]:
        print(hex(k), m.get(k))
