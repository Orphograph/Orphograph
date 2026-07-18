(function(){
  "use strict";
  var drop = document.getElementById("drop");
  var input = document.getElementById("file");
  var pick = document.getElementById("pick");
  var hint = document.getElementById("hint");
  var receipt = document.getElementById("receipt");

  pick.addEventListener("click", function(){ input.click(); });
  ["dragenter","dragover"].forEach(function(e){ drop.addEventListener(e, function(ev){ ev.preventDefault(); drop.classList.add("drag"); }); });
  ["dragleave","drop"].forEach(function(e){ drop.addEventListener(e, function(ev){ ev.preventDefault(); drop.classList.remove("drag"); }); });
  drop.addEventListener("drop", function(ev){ if (ev.dataTransfer.files && ev.dataTransfer.files[0]) anchorFile(ev.dataTransfer.files[0]); });
  input.addEventListener("change", function(){ if (input.files && input.files[0]) anchorFile(input.files[0]); });

  function hex(buf){ return [].map.call(new Uint8Array(buf), function(b){ return b.toString(16).padStart(2,"0"); }).join(""); }

  async function anchorFile(file){
    pick.disabled = true;
    hint.textContent = "Hashing “" + file.name + "” locally… (not uploading)";
    try{
      var buf = await file.arrayBuffer();
      var s256 = hex(await crypto.subtle.digest("SHA-256", buf));
      var s512 = hex(await crypto.subtle.digest("SHA-512", buf));
      hint.textContent = "Anchoring the fingerprint to Bitcoin…";
      var r = await fetch("/api/anchor", {
        method:"POST", credentials:"same-origin",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ hash_hex:s256, sha512_hex:s512 })
      });
      var d = await r.json().catch(function(){ return {}; });
      if (!r.ok || !d.receipt_id){
        var msg = (d && d.error) ? d.error : ("HTTP " + r.status);
        if (r.status === 429) msg = "Free-tier limit reached for today — grab a pack above to keep anchoring.";
        hint.textContent = msg;
        pick.disabled = false;
        return;
      }
      // Build the receipt with safe DOM nodes (textContent) + a self-constructed,
      // sanitized href — no innerHTML, so server values can't inject markup.
      var id = String(d.receipt_id || "").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64);
      var url = "/r/" + id;
      var cok = Number(d.calendars_ok); if (!isFinite(cok)) cok = "?";
      var ctot = Number(d.calendars_total) || 5;
      receipt.textContent = "";
      function line(label, value){
        var div = document.createElement("div");
        if (label){ var b = document.createElement("b"); b.textContent = label; div.appendChild(b); }
        if (value instanceof Node) div.appendChild(value);
        else if (value != null) div.appendChild(document.createTextNode(String(value)));
        return div;
      }
      var head = document.createElement("div");
      var ok = document.createElement("span"); ok.className = "ok"; ok.textContent = "✓ Anchored.";
      head.appendChild(ok);
      head.appendChild(document.createTextNode(" A receipt now ties this file to Bitcoin."));
      receipt.appendChild(head);
      receipt.appendChild(line("file: ", file.name));
      receipt.appendChild(line("sha-256: ", s256.slice(0, 40) + "…"));
      receipt.appendChild(line("calendars: ", cok + "/" + ctot + " confirmed"));
      var link = document.createElement("a"); link.href = url; link.textContent = "orphograph.com" + url;
      receipt.appendChild(line("receipt: ", link));
      var foot = document.createElement("div"); foot.className = "receipt-foot";
      foot.appendChild(document.createTextNode("Keep this. It verifies against Bitcoin forever — "));
      var vl = document.createElement("a"); vl.href = "/verify/"; vl.textContent = "even without us";
      foot.appendChild(vl); foot.appendChild(document.createTextNode("."));
      receipt.appendChild(foot);
      receipt.classList.add("show");
      hint.textContent = "That one's on the house. Anchor a whole backlog with a pack above — paid in crypto.";
      pick.textContent = "Anchor another →";
      pick.disabled = false;
    }catch(err){
      hint.textContent = "Something went wrong hashing the file. Try a different one.";
      pick.disabled = false;
    }
  }
})();
