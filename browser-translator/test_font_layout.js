'use strict';
const assert = require('assert/strict'), fs = require('fs');
const {monmusuCnCreateBridge} = require('./monmusu_cn_shim.js');
const RT = require('./monmusu_cn_runtime_config.js');
const b = monmusuCnCreateBridge(), mem = new WebAssembly.Memory({initial:4}); b.setMemory(mem);
// The field offsets this diagnostic reads are no longer baked into the shim;
// they come from the generated per-build config, so the test feeds the real one.
assert.ok(b.applyRuntimeConfig(RT.branches['0']), 'runtime config accepted');
const v = new DataView(mem.buffer), bytes = new Uint8Array(mem.buffer), p = 0x4000, face = 0x5000, info = 0x6000;
function klass(obj, ptr, namePtr, name) {
  v.setUint32(obj, ptr, true); v.setUint32(ptr+8, namePtr, true);
  for (let i=0;i<name.length;i++) bytes[namePtr+i]=name.charCodeAt(i);
}
klass(p,0x2000,0x2100,'TextMeshProUGUI'); v.setUint32(p+8,1234,true);
klass(face,0x2200,0x2300,'TMP_FontAsset');v.setUint32(face+8,1234,true);
klass(info,0x2400,0x2500,'TMP_TextInfo');
v.setUint32(p+0x90,face,true);v.setUint32(p+0x2a0,info,true);
for (const [off,val] of [[0x148,13.25],[0x14c,13.25],[0x150,14],[0x1a4,8],[0x1a8,14],
 [0x1ec,-1],[0x1fc,-85],[0x294,272.666],[0x298,68.52],[0x314,1]]) v.setFloat32(p+off,val,true);
bytes[p+0x18c]=1; bytes[p+0x21c]=1;v.setUint32(p+0x224,3,true);v.setUint32(p+0x228,99,true);
v.setUint32(info+0x20,4,true);v.setUint32(info+0xc,23,true);
v.setUint32(face+0x38,48,true);v.setFloat32(face+0x3c,1,true);v.setFloat32(face+0x44,96,true);
v.setFloat32(face+0x48,42.24,true);v.setFloat32(face+0x58,-5.76,true);v.setUint32(face+0xb8,9,true);
const before = Buffer.from(bytes), got = b.fontLayout(p);
assert.equal(got.fontSize,13.25);assert.equal(got.fontSizeMax,14);assert.equal(got.enableAutoSizing,true);
assert.equal(got.overflowMode,3);assert.equal(got.renderedLineCount,4);assert.equal(got.renderedCharacterCount,23);
assert.equal(got.faceInfo.pointSize,48);assert.equal(got.faceInfo.padding,9);assert.equal(got.fontScaleMultiplier,1);
assert.equal(got.lineSpacing,-85);assert.deepEqual(Buffer.from(bytes),before);
assert.ok(b.fontLayout(8).error);assert.deepEqual(b.fontLayouts(),[]);
// Only a translated owner seen by existing hooks enters the bounded census.
b.setUiIndex(JSON.parse(fs.readFileSync('resources/monmusu_cn_ui_index.json','utf8')));b.fontDisable();
b.handleContext(p,b.TAG_CONTEXT|b.CMD.UI_OWNER);
v.setUint32(0x7008,2,true);v.setUint16(0x700c,'確'.charCodeAt(0),true);v.setUint16(0x700e,'認'.charCodeAt(0),true);
b.handle(0x7000,b.UI_TAG);
assert.equal(b.fontLayouts().length,1);assert.equal(b.fontLayouts()[0].component,p);
console.log('15 font layout diagnostic checks passed (simulated memory; browser NOT RUN)');
