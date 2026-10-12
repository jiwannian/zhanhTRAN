'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.join(__dirname,'..');
test('independent startup never loads the original bundle or preferences',()=>{
 const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json')));
 assert.equal(manifest.name,'zhanhTRAN');assert.equal(manifest.version,'1.0.1');
 assert.equal(manifest.applications.zotero.id,'zhanhtran@zhanh.local');
 assert.equal(manifest.applications.zotero.update_url,'https://zhanhtran.invalid/update.json');
 const bootstrap=fs.readFileSync(path.join(root,'bootstrap.js'),'utf8');
 assert.match(bootstrap,/Zotero\.zhanhTRAN/);assert.match(bootstrap,/applyBackgroundUpdates/);
 assert.doesNotMatch(bootstrap,/scripts\/zhanhtran\.js|Zotero\.zhanhtran\.hooks|preferences\.js/);
 for(const size of [16,32,48,96,128])assert.ok(fs.statSync(path.join(root,`icons/icon-${size}.png`)).size>100);
});
