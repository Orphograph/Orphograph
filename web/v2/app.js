  const picker   = document.getElementById('picker');
  const center   = document.getElementById('ringCenter');
  const receipt  = document.getElementById('receipt');
  const hashing  = document.getElementById('hashing');
  const rFname   = document.getElementById('rFname');
  const rHash    = document.getElementById('rHash');
  const rTs      = document.getElementById('rTs');
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

  // drag styling
  ['dragenter','dragover'].forEach(e=>center.addEventListener(e,ev=>{ev.preventDefault();center.classList.add('drag');}));
  ['dragleave','drop'].forEach(e=>center.addEventListener(e,ev=>{ev.preventDefault();center.classList.remove('drag');}));
  center.addEventListener('drop',ev=>{ if(ev.dataTransfer.files[0]) handleFile(ev.dataTransfer.files[0]); });
  picker.addEventListener('change',()=>{ if(picker.files[0]) handleFile(picker.files[0]); });

  async function handleFile(file){
    if(busy) return;
    busy = true;
    hashing.textContent = 'hashing locally…';
    let hex;
    try{
      const buf = await file.arrayBuffer();
      const digest = await crypto.subtle.digest('SHA-256', buf);
      hex = [...new Uint8Array(digest)].map(b=>b.toString(16).padStart(2,'0')).join('');
    }catch(err){
      // crypto.subtle unavailable (e.g. non-secure context) — never display a fabricated hash
      hashing.textContent = 'SHA-256 unavailable in this browser context — try the live site.';
      busy = false;
      return;
    }
    // brief reveal of the fingerprint forming
    hashing.textContent = hex.slice(0,16) + '…';
    setTimeout(()=>{
      rFname.textContent = file.name;
      rHash.innerHTML = '<strong class="hash-label">SHA-256</strong><br>'+hex;
      const d = new Date();
      rTs.textContent = 'hashed locally ' + d.toISOString().slice(0,16).replace('T',' ') + ' UTC';
      center.style.display = 'none';
      receipt.classList.add('show');
      if(announce) announce.textContent = 'Fingerprint computed for ' + file.name;
      btnAgain?.focus();
      picker.value = '';
      busy = false;
    }, 650);
  }

  function resetDemo(){
    receipt.classList.remove('show');
    center.style.display = 'flex';
    hashing.textContent = '';
    if(announce) announce.textContent = '';
    picker.value = '';
    btnChoose?.focus();
  }
