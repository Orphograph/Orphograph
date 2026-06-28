const picker   = document.getElementById('picker');
  const center   = document.getElementById('ringCenter');
  const receipt  = document.getElementById('receipt');
  const hashing  = document.getElementById('hashing');
  const rFname   = document.getElementById('rFname');
  const rHash    = document.getElementById('rHash');
  const rTs      = document.getElementById('rTs');

  // drag styling
  ['dragenter','dragover'].forEach(e=>center.addEventListener(e,ev=>{ev.preventDefault();center.classList.add('drag');}));
  ['dragleave','drop'].forEach(e=>center.addEventListener(e,ev=>{ev.preventDefault();center.classList.remove('drag');}));
  center.addEventListener('drop',ev=>{ if(ev.dataTransfer.files[0]) handleFile(ev.dataTransfer.files[0]); });
  picker.addEventListener('change',()=>{ if(picker.files[0]) handleFile(picker.files[0]); });

  async function handleFile(file){
    hashing.textContent = 'hashing locally…';
    let hex;
    try{
      const buf = await file.arrayBuffer();
      const digest = await crypto.subtle.digest('SHA-256', buf);
      hex = [...new Uint8Array(digest)].map(b=>b.toString(16).padStart(2,'0')).join('');
    }catch(err){
      // fallback if crypto.subtle unavailable (e.g. non-secure context)
      hex = 'demo'.padEnd(64,'0');
    }
    // brief reveal of the fingerprint forming
    hashing.textContent = hex.slice(0,16) + '…';
    setTimeout(()=>{
      rFname.textContent = file.name;
      rHash.innerHTML = '<strong style="color:#C9BEA6">SHA-256</strong><br>'+hex;
      const d = new Date();
      rTs.textContent = 'existed on or before ' + d.toISOString().slice(0,16).replace('T',' ') + ' UTC';
      center.style.display = 'none';
      receipt.classList.add('show');
    }, 650);
  }

  function resetDemo(){
    receipt.classList.remove('show');
    center.style.display = 'flex';
    hashing.textContent = '';
    picker.value = '';
  }
