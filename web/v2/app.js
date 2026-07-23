// Pack-code bounce: a buyer's registration email points at {SITE_URL}/#pack=…
// but this lean homepage anchor flow does not spend pack credits. Redirect any
// such landing to /pack (the canonical pack surface) so the code is honoured
// and remaining anchors can actually be drawn. Fires only when a pack code is
// present in the URL; touches no credits/payment/anchor logic.
(function(){
  try{
    var pack='';
    var hp=new URLSearchParams((location.hash||'').replace(/^#/,''));
    if(hp.get('pack')) pack=hp.get('pack');
    if(!pack){ var qs=new URLSearchParams(location.search); if(qs.get('pack')) pack=qs.get('pack'); }
    if(pack && /^pk_[A-Za-z0-9_-]+$/.test(pack)){
      location.replace('/pack#pack='+encodeURIComponent(pack));
    }
  }catch(e){/* never block the homepage on a bounce failure */}
})();

const picker   = document.getElementById('picker');
const center   = document.getElementById('ringCenter');
const receipt  = document.getElementById('receipt');
const hashing  = document.getElementById('hashing');
const rFname   = document.getElementById('rFname');
const rHash    = document.getElementById('rHash');
const rTs      = document.getElementById('rTs');
const rLink    = document.getElementById('rLink');
const announce = document.getElementById('announce');
const btnChoose = document.getElementById('btnChoose');
const btnAgain  = document.getElementById('btnAgain');
let busy = false;

// null-guarded: survive any cached-HTML/JS version skew
document.getElementById('ctaAnchor')?.addEventListener('click',()=>picker.click());
btnChoose?.addEventListener('click',()=>picker.click());
btnAgain?.addEventListener('click',resetDemo);

// a missed drop outside the ring must not navigate the page away to the file
window.addEventListener('dragover',ev=>ev.preventDefault());
window.addEventListener('drop',ev=>ev.preventDefault());

['dragenter','dragover'].forEach(e=>center.addEventListener(e,ev=>{ev.preventDefault();center.classList.add('drag');}));
['dragleave','drop'].forEach(e=>center.addEventListener(e,ev=>{ev.preventDefault();center.classList.remove('drag');}));
center.addEventListener('drop',ev=>{ if(ev.dataTransfer.files[0]) handleFile(ev.dataTransfer.files[0]); });
picker.addEventListener('change',()=>{ if(picker.files[0]) handleFile(picker.files[0]); });

function hexOf(buf){ return [...new Uint8Array(buf)].map(b=>b.toString(16).padStart(2,'0')).join(''); }
function fail(msg){ hashing.textContent = msg; busy = false; }

async function handleFile(file){
  if(busy) return;
  busy = true;
  if(!(window.crypto && crypto.subtle)){
    fail('SHA-256 unavailable in this browser context.');
    return;
  }
  hashing.textContent = 'hashing locally…';
  let sha256, sha512;
  try{
    const buf = await file.arrayBuffer();
    const [d256, d512] = await Promise.all([
      crypto.subtle.digest('SHA-256', buf),
      crypto.subtle.digest('SHA-512', buf),
    ]);
    sha256 = hexOf(d256); sha512 = hexOf(d512);
  }catch(err){
    fail("Couldn't read that — folders aren't supported here; try a single file.");
    return;
  }
  // only the fingerprint travels; the file stays on the device
  hashing.textContent = sha256.slice(0,16) + '… anchoring via OpenTimestamps…';
  let resp;
  try{
    resp = await fetch('/api/anchor',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({hash_hex: sha256, sha512_hex: sha512, client_label: 'v2-dark'})
    });
  }catch(err){
    fail('Network hiccup — nothing was recorded. Your file never left your device; try again.');
    return;
  }
  if(resp.status === 429){
    fail("Today's three free anchors are used — a Writer Pack (10 anchors, $19) removes the limit. Pricing is just below.");
    return;
  }
  if(resp.status === 503){
    fail('Anchoring is briefly paused for maintenance — try again shortly.');
    return;
  }
  if(!resp.ok){
    fail('The anchor could not be recorded (server said '+resp.status+'). Try again in a moment.');
    return;
  }
  let j;
  try{ j = await resp.json(); }catch(err){
    fail('Unexpected reply from the server — try again in a moment.');
    return;
  }
  const d = new Date();
  rFname.textContent = file.name;
  rHash.innerHTML = '<strong class="hash-label">SHA-256</strong><br>'+sha256;
  rTs.textContent = 'sealed ' + d.toISOString().slice(0,16).replace('T',' ') + ' UTC · calendars ' +
                    (j.calendars_ok || 0) + '/' + (j.calendars_total || 5);
  if(rLink && j.receipt_id){
    rLink.href = '/r/' + encodeURIComponent(j.receipt_id);
    rLink.hidden = false;
  }
  hashing.textContent = '';
  center.style.display = 'none';
  receipt.classList.add('show');
  if(announce) announce.textContent = 'Anchored ' + file.name + ' — receipt ready.';
  btnAgain?.focus();
  picker.value = '';
  busy = false;
}

function resetDemo(){
  receipt.classList.remove('show');
  center.style.display = 'flex';
  hashing.textContent = '';
  if(announce) announce.textContent = '';
  if(rLink){ rLink.hidden = true; rLink.removeAttribute('href'); }
  picker.value = '';
  btnChoose?.focus();
}
