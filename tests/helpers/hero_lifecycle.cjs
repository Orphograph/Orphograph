const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/hero-envelope.js', 'utf8');
function environment(reduced=false, unavailable=false) {
  let next=0, time=100;
  const frames=new Map(), surfaces=[];
  class Element {
    constructor(rect={left:0,top:0,width:420,height:700}) {
      this.rect=rect; this.style={}; this.attrs={}; this.events={};
      const classes=new Set();
      this.classList={add:(...v)=>v.forEach(x=>classes.add(x)),remove:(...v)=>v.forEach(x=>classes.delete(x)),contains:x=>classes.has(x),toggle:(x,v)=>v?classes.add(x):classes.delete(x)};
      this.clientWidth=420;this.offsetHeight=700;
    }
    setAttribute(k,v){this.attrs[k]=v;}
    addEventListener(k,v){this.events[k]=v;}
    getBoundingClientRect(){return {...this.rect,right:this.rect.left+this.rect.width,bottom:this.rect.top+this.rect.height};}
    querySelector(){return null;}
    querySelectorAll(){return [];}
    appendChild(x){surfaces.push(x);}
    remove(){const i=surfaces.indexOf(this);if(i>=0)surfaces.splice(i,1);}
    focus(){}
  }
  const plate=new Element(), receipt=new Element({left:32,top:18,width:356,height:700}), button=new Element(), envelope=new Element({left:0,top:460,width:420,height:260}), action={};
  plate.querySelector=()=>envelope;button.querySelector=()=>action;
  const ctx={scale(){},createLinearGradient(){return {addColorStop(){}}},fillRect(){},strokeRect(){},beginPath(){},stroke(){},setLineDash(){},moveTo(){},lineTo(){},save(){},restore(){},clearRect(){},closePath(){},clip(){},drawImage(){}};
  const doc={events:{},getElementById:id=>({'hero-envelope':plate,'hero-envelope-toggle':button,'hero-sample-receipt':receipt}[id]),
    createElement:()=>{const e=new Element();e.getContext=()=>unavailable?null:ctx;return e;},
    createTreeWalker:()=>({nextNode:()=>null}),addEventListener(k,v){this.events[k]=v;}};
  const win={events:{},addEventListener(k,v){this.events[k]=v;}};
  const sandbox={document:doc,window:win,matchMedia:()=>({matches:reduced,addEventListener(){}}),devicePixelRatio:1,NodeFilter:{SHOW_TEXT:4},requestAnimationFrame:cb=>{frames.set(++next,cb);return next;},cancelAnimationFrame:id=>frames.delete(id)};
  vm.runInNewContext(source,sandbox);
  function step(){time+=20;const pending=[...frames.values()];frames.clear();pending.forEach(fn=>fn(time));}
  return {plate,receipt,button,surfaces,frames,win,doc,click:()=>button.events.click(),step,finish:()=>{for(let i=0;i<100;i++)step();}};
}
for(const [reduced,unavailable] of [[true,false],[false,true]]) {
  const e=environment(reduced,unavailable);e.click();
  assert.equal(e.button.attrs['aria-expanded'],'true');assert.ok(e.plate.classList.contains('is-open'));assert.equal(e.surfaces.length,0);
  e.click();assert.equal(e.button.attrs['aria-expanded'],'false');assert.equal(e.surfaces.length,0);
}
{
 const e=environment();e.click();for(let i=0;i<20;i++)e.step();
 assert.equal(e.surfaces.length,1);assert.ok(e.plate.classList.contains('is-warp'));
 e.click();e.finish();assert.equal(e.surfaces.length,0);assert.equal(e.button.attrs['aria-expanded'],'false');
 e.click();e.finish();assert.equal(e.button.attrs['aria-expanded'],'true');assert.equal(e.surfaces.length,0);
 e.doc.events.keydown({key:'Escape'});e.finish();assert.equal(e.button.attrs['aria-expanded'],'false');
 e.click();e.step();e.win.events.resize();assert.equal(e.surfaces.length,0);assert.equal(e.button.attrs['aria-expanded'],'true');
}
console.log('lifecycle: reduced motion, missing canvas, reversal, completion, Escape, resize pass');
