/* AnimeFlix — MovieBox-style UI (bottom nav, poster grid, hero banner) */
(() => {
  const $ = id => document.getElementById(id);

  // ---------- state ----------
  let CONFIG = { app_name: "AnimeFlix", google_client_id: "", demo_mode: true, telegram_connected: false };
  let TOKEN = localStorage.getItem("af_token") || "";
  let ME = null;
  let LIB = [];
  let page = "home";
  let query = "", category = "All";
  let currentPl = null, curSeason = 0, PP = null;
  const EMOJIS = ["🗡️","🌸","🤖","🍜","🍥","⚡","🐉","🔥","🌙","🎭","💥","🚀","🥷","🎮","👻","👑","🎬","📺"];
  const GRADS = ["linear-gradient(160deg,#1c2648,#48203a)","linear-gradient(160deg,#3a1c48,#1c4832)",
    "linear-gradient(160deg,#123048,#3a1236)","linear-gradient(160deg,#483a12,#124836)",
    "linear-gradient(160deg,#2a1e4a,#1e4a3a)","linear-gradient(160deg,#12313a,#3a1223)"];
  const LS = "animeflix_state_v2";
  let S = loadS();
  function loadS(){ try { return JSON.parse(localStorage.getItem(LS)) || {later:[],history:{},watched:{}}; } catch(e){ return {later:[],history:{},watched:{}}; } }
  function saveS(){ localStorage.setItem(LS, JSON.stringify(S)); }

  // ---------- stream/thumb URL helpers (preview me patch hote hain) ----------
  function streamUrl(epId,q){ return "/api/stream/" + epId + (q ? "?q=" + encodeURIComponent(q) : ""); }
  function thumbUrl(plId){ return "/api/thumb/" + plId; }

  // ---------- helpers ----------
  function toast(msg){ const t=$("toast"); t.textContent=msg; t.classList.add("show"); clearTimeout(t._h); t._h=setTimeout(()=>t.classList.remove("show"),2600); }
  function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
  function fmtDur(sec){ if(!sec) return ""; const m=Math.floor(sec/60),s=Math.round(sec%60); return m+":"+String(s).padStart(2,"0"); }
  function fmtMB(sec){ return sec>90 ? "Movie" : fmtDur(sec); }
  function fmtViews(n){ n=n||0; if(n>=1e6) return (n/1e6).toFixed(1)+"M views"; if(n>=1e3) return (n/1e3).toFixed(1)+"K views"; return n+(n===1?" view":" views"); }
  async function api(path, opts={}){
    opts.headers = Object.assign({}, opts.headers||{});
    if(TOKEN) opts.headers["Authorization"] = "Bearer "+TOKEN;
    if(opts.json){ opts.headers["Content-Type"]="application/json"; opts.body=JSON.stringify(opts.json); opts.method=opts.method||"POST"; }
    const res = await fetch(path, opts);
    let data = {};
    try { data = await res.json(); } catch(e){}
    if(!res.ok) throw new Error(data.detail || ("Error "+res.status));
    return data;
  }
  function readThumb(file, cb){
    const r = new FileReader();
    r.onload = () => { const img = new Image();
      img.onload = () => { const c=document.createElement("canvas"); c.width=480; c.height=720;
        const ctx=c.getContext("2d"); ctx.drawImage(img,0,0,480,720); cb(c.toDataURL("image/jpeg",0.82)); };
      img.src = r.result; };
    r.readAsDataURL(file);
  }
  function gradFor(pl){ return GRADS[(pl.id||0) % GRADS.length]; }

  // ---------- config/auth ----------
  async function loadConfig(){
    try { CONFIG = await api("/api/config"); } catch(e){}
    $("logoHome").textContent = "▶ " + (CONFIG.app_name||"AnimeFlix");
    document.title = CONFIG.app_name||"AnimeFlix";
    if(TOKEN){ try { const r = await api("/api/me"); ME = r.user; } catch(e){ TOKEN=""; localStorage.removeItem("af_token"); } }
    renderAuthUI();
    if(!CONFIG.google_client_id) $("demoLogin").classList.remove("hidden");
  }
  function renderAuthUI(){
    if(ME){
      $("signinBtn").classList.add("hidden");
      $("userAvatar").classList.remove("hidden");
      $("userAvatar").textContent = (ME.name||"U")[0].toUpperCase();
      $("adminBtn").classList.toggle("hidden", !ME.is_admin);
    } else {
      $("signinBtn").classList.remove("hidden");
      $("userAvatar").classList.add("hidden");
      $("adminBtn").classList.add("hidden");
    }
  }
  function initGoogle(){
    if(!CONFIG.google_client_id || !window.google) return;
    $("googleBtnWrap").classList.remove("hidden");
    google.accounts.id.initialize({
      client_id: CONFIG.google_client_id,
      callback: async (resp) => {
        try {
          const r = await api("/api/auth/google", {json:{credential: resp.credential}});
          TOKEN = r.token; localStorage.setItem("af_token", TOKEN);
          const me = await api("/api/me"); ME = me.user;
          renderAuthUI(); $("signinModal").classList.add("hidden");
          toast("Welcome, "+r.name+"! 🎉"); refresh();
        } catch(e){ $("signinErr").textContent = e.message; }
      },
    });
    google.accounts.id.renderButton($("googleBtnWrap"), {theme:"filled_black", size:"large", width:320});
  }
  $("signinBtn").onclick = () => { $("signinModal").classList.remove("hidden"); $("signinErr").textContent="";
    setTimeout(initGoogle, 100); };
  $("signinCancel").onclick = () => $("signinModal").classList.add("hidden");
  $("demoBtn").onclick = async () => {
    const n = $("nameInput").value.trim();
    if(!n){ $("signinErr").textContent="Naam likh do"; return; }
    try {
      const r = await api("/api/auth/demo", {json:{name:n}});
      TOKEN = r.token; localStorage.setItem("af_token", TOKEN);
      const me = await api("/api/me"); ME = me.user;
      renderAuthUI(); $("signinModal").classList.add("hidden"); $("nameInput").value="";
      toast("Welcome, "+r.name+"! 🎉"); refresh();
    } catch(e){ $("signinErr").textContent = e.message; }
  };
  $("userAvatar").onclick = () => { if(confirm("Sign out?")){ TOKEN=""; ME=null; localStorage.removeItem("af_token");
    renderAuthUI(); goHome(); } };

  // ---------- posters/cards ----------
  function makeCard(pl){
    const card=document.createElement("div"); card.className="card";
    const poster=document.createElement("div"); poster.className="poster";
    poster.style.background = pl.has_thumb ? "#111" : gradFor(pl);
    poster.innerHTML = (pl.has_thumb ? `<img src="${thumbUrl(pl.id)}" alt="" onerror="this.remove()">`
                                     : `<div class="emoji">${esc(pl.emoji||"🎬")}</div>`)
      + '<div class="shade"></div>'
      + `<div class="badge rate">★ ${pl.rating || "–"}</div>`
      + `<div class="badge">S${pl.seasons||0}·${pl.episodes||0}EP</div>`;
    const title=document.createElement("div"); title.className="card-title"; title.textContent=pl.title;
    const meta=document.createElement("div"); meta.className="card-meta";
    meta.textContent=fmtViews(pl.views);
    card.append(poster,title,meta);
    card.onclick=()=>renderPlaylist(pl.id);
    return card;
  }

  async function loadLib(){ try { LIB = await api("/api/playlists"); } catch(e){ LIB=[]; } }
  async function refresh(){ await loadLib(); if(page==="home") renderHome(); else if(page==="search") renderSearch(); else if(page==="saved") renderSaved(); }

  // ---------- nav ----------
  function setNav(id){ ["navHome","navSearch","navSaved","navProfile"].forEach(n=>$(n).classList.remove("active"));
    if(id) $(id).classList.add("active"); }
  function goHome(){ page="home"; setNav("navHome"); renderHome(); }
  $("navHome").onclick=goHome;
  $("navSearch").onclick=()=>{ page="search"; setNav("navSearch"); renderSearch(); };
  $("navSaved").onclick=()=>{ page="saved"; setNav("navSaved"); renderSaved(); };
  $("navProfile").onclick=()=>{ page="profile"; setNav("navProfile"); renderProfile(); };
  $("logoHome").onclick=goHome;

  // ---------- HOME ----------
  function renderHome(){
    PP=null; $("main").innerHTML="";
    const wrap=document.createElement("div");

    // hero: sabse popular playlist
    if(LIB.length){
      const hero=[...LIB].sort((a,b)=>(b.views||0)-(a.views||0))[0];
      const heroDiv=document.createElement("div"); heroDiv.className="hero";
      heroDiv.style.background=gradFor(hero);
      heroDiv.innerHTML = (hero.has_thumb?`<img src="${thumbUrl(hero.id)}">`:`<div class="h-emoji">${esc(hero.emoji||"🎬")}</div>`)
        + '<div class="h-shade"></div>'
        + `<div class="h-body"><div class="h-title">${esc(hero.title)}</div>
           <div class="h-meta">⭐ ${hero.rating||"–"} · ${hero.category} · ${hero.episodes} episodes · ${fmtViews(hero.views)}</div>
           <button class="btn accent">▶ Play</button>
           <span class="badge rate" style="position:static;display:inline-block;margin-left:2px;background:rgba(0,0,0,.55);color:var(--gold);padding:6px 10px;border-radius:8px;font-size:.72rem">★ Trending</span></div>`;
      heroDiv.onclick=()=>renderPlaylist(hero.id);
      wrap.appendChild(heroDiv);
    }

    // categories
    const cats=["All",...new Set(LIB.map(p=>p.category||"Other"))];
    if(cats.length>1){
      const chips=document.createElement("div"); chips.className="chips";
      cats.forEach(c=>{ const ch=document.createElement("div"); ch.className="chip"+(c===category?" active":"");
        ch.textContent=c; ch.onclick=()=>{ category=c; renderHome(); }; chips.appendChild(ch); });
      wrap.appendChild(chips);
    }

    // continue watching
    const cont=Object.entries(S.history).filter(([k,h])=>h.pos>5 && h.dur && h.pos<h.dur*0.95 && h.pid);
    if(cont.length){
      const t=document.createElement("div"); t.className="row-title"; t.textContent="▶ Continue Watching";
      const row=document.createElement("div"); row.className="hscroll";
      cont.sort((a,b)=>b[1].at-a[1].at).forEach(([k,h])=>{
        const pl=LIB.find(p=>p.id===h.pid); if(!pl) return;
        const card=makeCard(pl);
        const prog=document.createElement("div"); prog.className="progress-line";
        prog.style.width=Math.min(100,h.pos/h.dur*100)+"%";
        card.querySelector(".poster").appendChild(prog);
        card.onclick=()=>goWatch(h.pid,h.season,h.ep);
        row.appendChild(card);
      });
      wrap.appendChild(t); wrap.appendChild(row);
    }

    // rows by category
    const list = LIB.filter(p=>category==="All"||p.category===category);
    if(category!=="All"){
      const t=document.createElement("div"); t.className="row-title"; t.textContent=category;
      const grid=document.createElement("div"); grid.className="grid";
      list.forEach(pl=>grid.appendChild(makeCard(pl)));
      wrap.appendChild(t); wrap.appendChild(grid);
    } else {
      // Trending + New rows phir All
      const trending=[...LIB].sort((a,b)=>(b.views||0)-(a.views||0)).slice(0,10);
      if(trending.length){
        const t=document.createElement("div"); t.className="row-title"; t.textContent="🔥 Trending Now";
        const row=document.createElement("div"); row.className="hscroll";
        trending.forEach(pl=>row.appendChild(makeCard(pl)));
        wrap.appendChild(t); wrap.appendChild(row);
      }
      const catsOnly=[...new Set(LIB.map(p=>p.category||"Other"))].slice(0,4);
      catsOnly.forEach(c=>{
        const rowPls=LIB.filter(p=>p.category===c);
        if(rowPls.length<2) return;
        const t=document.createElement("div"); t.className="row-title"; t.textContent=c;
        const row=document.createElement("div"); row.className="hscroll";
        rowPls.forEach(pl=>row.appendChild(makeCard(pl)));
        wrap.appendChild(t); wrap.appendChild(row);
      });
      if(LIB.length){
        const t=document.createElement("div"); t.className="row-title"; t.textContent="📚 All Playlists";
        const grid=document.createElement("div"); grid.className="grid";
        LIB.forEach(pl=>grid.appendChild(makeCard(pl)));
        wrap.appendChild(t); wrap.appendChild(grid);
      }
    }

    if(!LIB.length){
      const e=document.createElement("div"); e.className="empty";
      e.innerHTML = CONFIG.telegram_connected
        ? '<div class="empty-state-icon">🎬</div>Abhi koi playlist nahi — channel pe video bhejo ya ＋ Add se banao'
        : '<div class="empty-state-icon">⚠️</div>Telegram bots offline — env vars check karo';
      wrap.appendChild(e);
    }
    $("main").appendChild(wrap);
  }

  // ---------- SEARCH ----------
  function renderSearch(){
    PP=null; $("main").innerHTML="";
    const wrap=document.createElement("div");
    const t=document.createElement("div"); t.className="page-title"; t.textContent="🔍 Search";
    const inp=document.createElement("input"); inp.className="search-bar"; inp.type="search";
    inp.placeholder="Playlist ka naam likho..."; inp.value=query;
    const results=document.createElement("div");
    function showResults(){
      results.innerHTML="";
      const q=query.trim().toLowerCase();
      const list=q?LIB.filter(p=>(p.title||"").toLowerCase().includes(q)):[];
      const grid=document.createElement("div"); grid.className="grid";
      if(!list.length){
        const e=document.createElement("div"); e.className="empty"; e.style.gridColumn="1/-1";
        e.innerHTML='<div class="empty-state-icon">🔍</div>'+(q?"Kuch nahi mila — dusra naam try karo":"Naam likho — playlist search hoga");
        grid.appendChild(e);
      } else list.forEach(pl=>grid.appendChild(makeCard(pl)));
      results.appendChild(grid);
    }
    inp.addEventListener("input",e=>{ query=e.target.value; showResults(); });
    showResults();
    wrap.append(t,inp,results);
    $("main").appendChild(wrap);
  }

  // ---------- SAVED ----------
  function renderSaved(){
    PP=null; $("main").innerHTML="";
    const wrap=document.createElement("div");
    const t=document.createElement("div"); t.className="page-title"; t.textContent="🔖 Saved";
    wrap.appendChild(t);

    const later=LIB.filter(p=>S.later.includes(p.id));
    const t2=document.createElement("div"); t2.className="row-title"; t2.textContent="Watch Later";
    const g1=document.createElement("div"); g1.className="grid";
    if(later.length) later.forEach(pl=>g1.appendChild(makeCard(pl)));
    else { const e=document.createElement("div"); e.className="empty"; e.style.gridColumn="1/-1";
      e.textContent="Playlist pe 🔖 dabao — yahan save hoga"; g1.appendChild(e); }
    wrap.append(t2,g1);

    const cont=Object.entries(S.history).filter(([k,h])=>h.pos>5&&h.dur&&h.pos<h.dur*0.95&&h.pid)
      .sort((a,b)=>b[1].at-a[1].at);
    const t3=document.createElement("div"); t3.className="row-title"; t3.textContent="▶ Continue Watching";
    const g2=document.createElement("div"); g2.className="grid";
    if(cont.length) cont.forEach(([k,h])=>{
      const pl=LIB.find(p=>p.id===h.pid); if(!pl) return;
      const card=makeCard(pl);
      const prog=document.createElement("div"); prog.className="progress-line";
      prog.style.width=Math.min(100,h.pos/h.dur*100)+"%";
      card.querySelector(".poster").appendChild(prog);
      card.onclick=()=>goWatch(h.pid,h.season,h.ep);
      g2.appendChild(card);
    });
    else { const e=document.createElement("div"); e.className="empty"; e.style.gridColumn="1/-1";
      e.textContent="Aadhi chhodi hui videos yahan aayengi"; g2.appendChild(e); }
    wrap.append(t3,g2);
    $("main").appendChild(wrap);
  }

  // ---------- PROFILE ----------
  function renderProfile(){
    PP=null; $("main").innerHTML="";
    const wrap=document.createElement("div");
    const t=document.createElement("div"); t.className="page-title"; t.textContent="👤 Profile";
    wrap.appendChild(t);

    const card=document.createElement("div"); card.className="profile-card";
    if(ME){
      card.innerHTML=`<div class="big-av">${esc((ME.name||"U")[0].toUpperCase())}</div>
        <h2 style="margin-bottom:6px">${esc(ME.name)}</h2>
        <p style="color:var(--muted);font-size:.85rem;margin-bottom:14px">${ME.is_admin?"👑 Admin":"Viewer"}</p>`;
      const so=document.createElement("button"); so.className="btn ghost"; so.textContent="Sign out";
      so.onclick=()=>{ if(confirm("Sign out?")){ TOKEN=""; ME=null; localStorage.removeItem("af_token");
        renderAuthUI(); goHome(); } };
      card.appendChild(so);
    } else {
      card.innerHTML=`<div class="big-av">?</div>
        <h2 style="margin-bottom:8px">Sign in karo</h2>
        <p style="color:var(--muted);font-size:.85rem;margin-bottom:14px">Comments, ratings aur saved playlists ke liye</p>`;
      const si=document.createElement("button"); si.className="btn accent"; si.textContent="Sign in";
      si.onclick=()=>{ $("signinModal").classList.remove("hidden"); $("signinErr").textContent="";
        setTimeout(initGoogle,100); };
      card.appendChild(si);
    }
    wrap.appendChild(card);
    $("main").appendChild(wrap);
  }

  // ---------- PLAYLIST PAGE ----------
  async function renderPlaylist(pid, season){
    let d;
    try { d = await api("/api/playlists/"+pid); } catch(e){ toast(e.message); return goHome(); }
    PP=null; page="playlist"; currentPl=d; curSeason=(season??((d.seasons[0]||{}).season??1));
    setNav(null); $("main").innerHTML="";
    const root=document.createElement("div");

    const back=document.createElement("button"); back.className="back"; back.textContent="← Back";
    back.onclick=goHome;

    const banner=document.createElement("div"); banner.className="pl-hero";
    if(d.has_thumb) banner.insertAdjacentHTML("beforeend",`<img class="backdrop" src="${thumbUrl(d.id)}">`);
    const poster=document.createElement("div"); poster.className="pl-poster";
    poster.innerHTML = d.has_thumb ? `<img src="${thumbUrl(d.id)}">` : esc(d.emoji||"🎬");
    if(ME && ME.is_admin){
      const eb=document.createElement("button"); eb.className="cover-edit"; eb.textContent="📷 Poster";
      eb.onclick=()=>{ $("bannerThumbInput")._pid=d.id; $("bannerThumbInput").click(); };
      poster.appendChild(eb);
    }
    const info=document.createElement("div"); info.className="pl-info";
    info.innerHTML=`
      <h1>${esc(d.title)}</h1>
      <div class="meta">⭐ ${d.rating||"–"} · ${esc(d.category)} · ${d.seasons.length} season · ${d.episodes} EP · ${fmtViews(d.views)}</div>
      <div class="pl-actions">
        <button class="btn accent" id="playFirst">▶ Play</button>
        <button class="btn ghost" id="wlBtn">${S.later.includes(d.id)?"🔖 Saved":"🔖 Save"}</button>
        ${ME&&ME.is_admin?`<button class="btn ghost small" id="delPl">🗑</button>`:""}
        <div class="stars" id="rateStars"></div>
        <span style="color:var(--muted);font-size:.78rem" id="rateHint"></span>
      </div>
      <div class="desc">${esc(d.desc||"")}</div>`;
    banner.append(poster,info);

    const sbar=document.createElement("div"); sbar.className="season-bar";
    const sel=document.createElement("select");
    d.seasons.forEach(x=>{ const o=document.createElement("option"); o.value=x.season; o.textContent="Season "+x.season; sel.appendChild(o); });
    sel.value=curSeason; sel.onchange=()=>renderPlaylist(pid,Number(sel.value));
    const cnt=document.createElement("span"); cnt.className="count";
    const cur=d.seasons.find(x=>x.season===curSeason);
    cnt.textContent=(cur?cur.episodes.length:0)+" episodes";
    sbar.append(sel,cnt);

    const eplist=document.createElement("div"); eplist.className="ep-list";
    (cur?cur.episodes:[]).forEach((e)=>{
      const row=document.createElement("div"); row.className="ep"+(S.watched[e.id]?" watched":"");
      const num=document.createElement("div"); num.className="ep-num"; num.textContent=e.ep_num;
      const ei=document.createElement("div"); ei.className="ep-info";
      const et=document.createElement("div"); et.className="ep-title";
      et.innerHTML=esc(e.ep_num+". "+e.title)+(e.has_link?'<span class="tg-tag">🔗 TG</span>':"");
      const em=document.createElement("div"); em.className="ep-meta";
      em.textContent=fmtMB(e.duration)+" · "+fmtViews(e.views);
      ei.append(et,em);
      const pb=document.createElement("div"); pb.className="ep-play"; pb.textContent="▶";
      row.append(num,ei,pb); row.onclick=()=>goWatch(pid,e.season,e.ep_num);
      eplist.appendChild(row);
    });

    const cm=document.createElement("div"); cm.className="comments";
    cm.innerHTML="<h3>💬 Comments ("+d.comments.length+")</h3>";
    if(ME){
      const inp=document.createElement("div"); inp.className="c-input";
      const ti=document.createElement("input"); ti.placeholder="Kuch likho...";
      const b=document.createElement("button"); b.className="btn accent"; b.textContent="Post";
      b.onclick=async ()=>{ if(!ti.value.trim())return;
        try{ await api("/api/comments",{json:{playlist_id:pid,text:ti.value.trim()}}); toast("Comment 💬"); renderPlaylist(pid,curSeason); }
        catch(err){ toast(err.message); } };
      inp.append(ti,b); cm.appendChild(inp);
    } else {
      const inp=document.createElement("div"); inp.className="hintbox";
      inp.textContent="Comment ke liye sign in karo (Profile tab)."; cm.appendChild(inp);
    }
    d.comments.forEach(c=>{
      const dv=document.createElement("div"); dv.className="comment";
      dv.innerHTML=`<div class="c-head"><div class="c-av">${esc((c.name||"U")[0].toUpperCase())}</div><div class="c-name">${esc(c.name)}</div></div><div class="c-text">${esc(c.text)}</div>`;
      cm.appendChild(dv);
    });

    root.append(back,banner,sbar,eplist,cm);
    $("main").appendChild(root);

    $("playFirst").onclick=()=>{ if(cur&&cur.episodes.length) goWatch(pid,cur.season,cur.episodes[0].ep_num); };
    $("wlBtn").onclick=()=>{ if(S.later.includes(d.id))S.later=S.later.filter(x=>x!==d.id); else S.later.push(d.id);
      saveS(); renderPlaylist(pid,curSeason); };
    const del=$("delPl"); if(del) del.onclick=async ()=>{
      if(!confirm("Playlist delete? (Telegram me video safe rahegi)")) return;
      try { await api("/api/playlists/"+d.id,{method:"DELETE"}); toast("Deleted"); await refresh(); goHome(); }
      catch(e){ toast(e.message); } };
    drawStars(d);
  }

  $("bannerThumbInput").addEventListener("change",function(){
    const f=this.files[0]; if(!f) return; const pid=this._pid; this.value="";
    readThumb(f,(dataUrl)=>{
      api("/api/playlists/"+pid,{method:"PATCH",json:{thumb:dataUrl}})
        .then(()=>{ toast("✅ Poster updated 📷"); if(page==="playlist")renderPlaylist(pid,curSeason); refresh(); })
        .catch(e=>toast(e.message));
    });
  });

  function drawStars(d){
    const box=$("rateStars"); box.innerHTML="";
    for(let i=1;i<=5;i++){
      const s=document.createElement("span"); s.className="st"+(i<=d.my_rating?" on":""); s.textContent="★";
      s.onclick=async ()=>{ if(!ME){ toast("Pehle sign in karo ⭐"); return; }
        try { await api("/api/rate",{json:{playlist_id:d.id,stars:i}}); toast(i+"★ saved!"); renderPlaylist(d.id,curSeason); }
        catch(e){ toast(e.message); } };
      box.appendChild(s);
    }
    $("rateHint").textContent = d.my_rating ? ("Tumne "+d.my_rating+"★ diya") :
      (d.rating_count ? (d.rating+"★ · "+d.rating_count+" votes") : "Rate karo");
  }

  // ---------- WATCH PAGE ----------
  function saveProgress(){
    const v=document.getElementById("player");
    if(PP && v && v.currentTime>0){
      S.history["e"+PP.ep_id]={pid:PP.pid,season:PP.season,ep:PP.ep,pos:v.currentTime,dur:v.duration||PP.dur,at:Date.now()};
      if(v.duration && v.currentTime>v.duration*0.9) S.watched[PP.ep_id]=1;
      saveS();
    }
  }
  function findEp(d,season,epnum){
    const sx=d.seasons.find(x=>x.season===season); if(!sx) return null;
    return sx.episodes.find(e=>e.ep_num===epnum)||sx.episodes[0];
  }
  async function goWatch(pid,season,epnum){
    let d; try { d=await api("/api/playlists/"+pid); } catch(e){ toast(e.message); return; }
    const e=findEp(d,season,epnum); if(!e){ toast("Episode nahi mili"); return; }
    saveProgress();
    PP={pid,season:e.season,ep:e.ep_num,ep_id:e.id,dur:e.duration,title:e.title};
    page="watch"; currentPl=d; curSeason=e.season; setNav(null);
    $("main").innerHTML=""; window.scrollTo(0,0);

    const root=document.createElement("div");
    const back=document.createElement("button"); back.className="back";
    back.textContent="← "+d.title; back.onclick=()=>renderPlaylist(pid,e.season);
    root.appendChild(back);

    const layout=document.createElement("div"); layout.className="watch-layout";
    const col=document.createElement("div"); col.className="watch-col";

    const pw=document.createElement("div"); pw.className="player-wrap";
    const v=document.createElement("video");
    v.id="player"; v.controls=true; v.playsInline=true; v.preload="metadata";
    const quals=e.qualities||[];
    let curQ=quals[0]||"";
    v.src=streamUrl(e.id,curQ);
    const ov=document.createElement("div"); ov.id="playOverlay"; ov.innerHTML='<div class="big">▶</div>';
    ov.classList.add("hidden");
    const tryPlay=()=>{ v.play().then(()=>ov.classList.add("hidden")).catch(()=>{
      setTimeout(()=>{ v.play().then(()=>ov.classList.add("hidden"))
        .catch(()=>toast("Video load ho rahi hai — 2-3 sec ruk ke ▶ dobara dabao")); },700); }); };
    ov.onclick=tryPlay;
    pw.append(v,ov);

    const h=S.history["e"+e.id];
    let qswitch=false, qpos=0, qplaying=false;
    v.addEventListener("loadedmetadata",()=>{
      if(qswitch){ qswitch=false; try{v.currentTime=qpos;}catch(err){} if(qplaying) tryPlay(); return; }
      if(h&&h.pos>5&&h.pos<v.duration*0.95){ try{v.currentTime=h.pos;}catch(err){} } });
    v.addEventListener("play",()=>ov.classList.add("hidden"));
    v.addEventListener("error",()=>{
      if(window.AF_NEXT_URL){ const nu=window.AF_NEXT_URL(v.currentSrc); if(nu){ v.src=nu; tryPlay(); return; } }
      const badfmt=/\.(mkv|avi|flv|wmv|mov|ts)\s*$/i.test(e.title||"");
      toast(badfmt
        ? "Ye format (MKV/AVI) browser me play NAHI hota — MP4 (H.264) version upload karo"
        : "Video load nahi hui — internet check karo");
    });
    v.addEventListener("ended",()=>{ toast("Auto-next..."); setTimeout(nextEpisode,900); });
    let lastSave=0;
    v.addEventListener("timeupdate",()=>{ const n=Date.now(); if(n-lastSave>4000){ lastSave=n; saveProgress(); } });
    col.appendChild(pw);

    if(quals.length>1){
      const qb=document.createElement("div"); qb.className="qbar";
      quals.forEach(q=>{
        const b=document.createElement("button");
        b.className="qchip"+(q===curQ?" on":""); b.textContent=q;
        b.onclick=()=>{
          if(q===curQ) return;
          qpos=v.currentTime; qplaying=!v.paused; qswitch=true;
          curQ=q; v.src=streamUrl(e.id,curQ); dl.href=streamUrl(e.id,curQ)+"?download=1";
          qb.querySelectorAll(".qchip").forEach(x=>x.classList.remove("on"));
          b.classList.add("on");
        };
        qb.appendChild(b);
      });
      const lab=document.createElement("span"); lab.className="qlab"; lab.textContent="Quality:";
      qb.prepend(lab);
      col.appendChild(qb);
    }

    const ttl=document.createElement("h1"); ttl.className="w-title"; ttl.textContent=e.title;
    const meta=document.createElement("div"); meta.className="w-meta";
    meta.textContent=d.title+" · S"+e.season+" E"+e.ep_num+" · "+fmtViews(e.views+1);
    col.append(ttl,meta);

    const acts=document.createElement("div"); acts.className="w-actions";
    const prev=document.createElement("button"); prev.className="btn ghost small"; prev.textContent="⏮ Prev"; prev.onclick=prevEpisode;
    const nextB=document.createElement("button"); nextB.className="btn accent small"; nextB.textContent="Next ⏭"; nextB.onclick=nextEpisode;
    const dl=document.createElement("a"); dl.className="btn ghost small"; dl.textContent="⬇ Download";
    dl.href=streamUrl(e.id,curQ)+"?download=1";
    const wl=document.createElement("button"); wl.className="btn ghost small";
    wl.textContent=S.later.includes(d.id)?"🔖 Saved":"🔖 Save";
    wl.onclick=()=>{ if(S.later.includes(d.id))S.later=S.later.filter(x=>x!==d.id); else S.later.push(d.id);
      saveS(); wl.textContent=S.later.includes(d.id)?"🔖 Saved":"🔖 Save"; };
    acts.append(prev,nextB,dl,wl); col.appendChild(acts);

    if(e.has_link){
      const note=document.createElement("div"); note.className="proxy-note";
      note.innerHTML="🔗 <b>Telegram link se aaya episode</b> — server proxy se stream ho raha hai (browser → server → Telegram).";
      col.appendChild(note);
    }

    const eb=document.createElement("div"); eb.className="season-bar"; eb.style.marginTop="6px";
    const sel=document.createElement("select");
    d.seasons.forEach(x=>{ const o=document.createElement("option"); o.value=x.season; o.textContent="Season "+x.season; sel.appendChild(o); });
    sel.value=e.season; sel.onchange=()=>goWatch(pid,Number(sel.value),1);
    const cnt=document.createElement("span"); cnt.className="count";
    const sx=d.seasons.find(x=>x.season===e.season);
    cnt.textContent=(sx?sx.episodes.length:0)+" episodes";
    eb.append(sel,cnt);
    const elist=document.createElement("div"); elist.className="ep-list";
    (sx?sx.episodes:[]).forEach(x=>{
      const row=document.createElement("div");
      row.className="ep"+(S.watched[x.id]?" watched":"")+(x.ep_num===e.ep_num?" current":"");
      const num=document.createElement("div"); num.className="ep-num"; num.textContent=x.ep_num;
      const ei=document.createElement("div"); ei.className="ep-info";
      const et=document.createElement("div"); et.className="ep-title";
      et.innerHTML=esc(x.ep_num+". "+x.title)+(x.ep_num===e.ep_num?'<span class="now-tag">▶ NOW</span>':"")+(x.has_link?'<span class="tg-tag">🔗 TG</span>':"");
      const em=document.createElement("div"); em.className="ep-meta";
      em.textContent=fmtMB(x.duration)+" · "+fmtViews(x.views);
      ei.append(et,em);
      const pb=document.createElement("div"); pb.className="ep-play"; pb.textContent="▶";
      row.append(num,ei,pb); row.onclick=()=>goWatch(pid,x.season,x.ep_num);
      elist.appendChild(row);
    });
    col.append(eb,elist);

    const cm=document.createElement("div"); cm.className="comments";
    cm.innerHTML="<h3>💬 Comments ("+d.comments.length+")</h3>";
    if(ME){
      const inp=document.createElement("div"); inp.className="c-input";
      const t=document.createElement("input"); t.placeholder="Kuch likho...";
      const b=document.createElement("button"); b.className="btn accent"; b.textContent="Post";
      b.onclick=async ()=>{ if(!t.value.trim())return;
        try{ await api("/api/comments",{json:{playlist_id:pid,text:t.value.trim()}}); toast("Comment 💬"); goWatch(pid,e.season,e.ep_num); }
        catch(err){ toast(err.message); } };
      inp.append(t,b); cm.appendChild(inp);
    } else {
      const inp=document.createElement("div"); inp.className="hintbox";
      inp.textContent="Comment ke liye sign in karo."; cm.appendChild(inp);
    }
    d.comments.forEach(c=>{
      const dv=document.createElement("div"); dv.className="comment";
      dv.innerHTML=`<div class="c-head"><div class="c-av">${esc((c.name||"U")[0].toUpperCase())}</div><div class="c-name">${esc(c.name)}</div></div><div class="c-text">${esc(c.text)}</div>`;
      cm.appendChild(dv);
    });
    col.appendChild(cm);

    const side=document.createElement("div");
    const st=document.createElement("div"); st.className="row-title"; st.style.marginTop="0";
    st.textContent="✨ More to Explore";
    side.appendChild(st);
    const sg=document.createElement("div"); sg.className="grid"; sg.style.gridTemplateColumns="repeat(auto-fill,minmax(95px,1fr))";
    LIB.filter(p=>p.id!==pid).slice(0,8).forEach(p=>sg.appendChild(makeCard(p)));
    if(!LIB.filter(p=>p.id!==pid).length) sg.innerHTML='<div class="hintbox">Aur playlists ＋ Add se banao</div>';
    side.appendChild(sg);

    layout.append(col,side); root.appendChild(layout);
    $("main").appendChild(root);

    tryPlay();
  }

  function nextEpisode(){
    if(!PP||!currentPl) return;
    const d=currentPl, sx=d.seasons.find(x=>x.season===PP.season);
    if(sx && PP.ep<Math.max(...sx.episodes.map(e=>e.ep_num))) goWatch(PP.pid,PP.season,PP.ep+1);
    else { const nx=d.seasons.find(x=>x.season>PP.season);
      if(nx){ toast("Season "+nx.season+" 🔥"); goWatch(PP.pid,nx.season,nx.episodes[0].ep_num); }
      else toast("Series khatam! 🎉"); }
  }
  function prevEpisode(){
    if(!PP||!currentPl) return;
    const d=currentPl, sx=d.seasons.find(x=>x.season===PP.season);
    if(sx && PP.ep>Math.min(...sx.episodes.map(e=>e.ep_num))) goWatch(PP.pid,PP.season,PP.ep-1);
    else { const pv=[...d.seasons].reverse().find(x=>x.season<PP.season);
      if(pv) goWatch(PP.pid,pv.season,Math.max(...pv.episodes.map(e=>e.ep_num))); }
  }

  // ---------- ADD VIDEO ----------
  let pendingThumb=null;
  function fillPlaylistSelect(){
    const sel=$("addPlaylist"); sel.innerHTML="";
    LIB.forEach(p=>{ const o=document.createElement("option"); o.value=p.id; o.textContent=p.title+" ("+p.category+")"; sel.appendChild(o); });
    const o=document.createElement("option"); o.value="new"; o.textContent="＋ New Playlist…"; sel.appendChild(o);
  }
  function fillEmojiSelect(){
    const sel=$("newPlEmoji");
    if(sel.options.length) return;
    EMOJIS.forEach(e=>{ const o=document.createElement("option"); o.value=e; o.textContent=e; sel.appendChild(o); });
  }
  const syncNewPl=()=>{ $("newPlFields").classList.toggle("hidden",$("addPlaylist").value!=="new"); };
  $("adminBtn").onclick=()=>{ fillPlaylistSelect(); fillEmojiSelect();
    $("thumbPrev").innerHTML="koi image nahi";
    pendingThumb=null; $("addErr").textContent=""; $("linkErr").textContent="";
    $("addModal").classList.remove("hidden"); syncNewPl(); };
  $("addCancel").onclick=()=>$("addModal").classList.add("hidden");
  $("addPlaylist").onchange=syncNewPl;
  $("newPlThumb").addEventListener("change",function(){
    const f=this.files[0]; if(!f) return;
    readThumb(f,(d)=>{ pendingThumb=d; $("thumbPrev").innerHTML='<img src="'+d+'">'; });
  });
  $("addBtn").onclick=async ()=>{
    const l480=$("addLink480").value.trim(), l720=$("addLink720").value.trim(), l1080=$("addLink1080").value.trim();
    const link=l480||l720||l1080;
    const items=[];
    if(l480) items.push({label:"480p", link:l480});
    if(l720) items.push({label:"720p", link:l720});
    if(l1080) items.push({label:"1080p", link:l1080});
    if(!items.length){ $("linkErr").textContent="Kam se kam ek Telegram link daalo (480p/720p/1080p)"; return; }
    $("linkErr").textContent=""; $("addErr").textContent="";
    const btn=$("addBtn"); btn.disabled=true; btn.textContent="Checking…";
    try{
      const body={ links:items, season:Number($("addSeason").value||1), ep_num:Number($("addEpNum").value||1),
        title:$("addEpTitle").value.trim() };
      if($("addPlaylist").value==="new"){
        const t=$("newPlTitle").value.trim();
        if(!t) throw new Error("New playlist ka title likho");
        body.new_playlist={ title:t, category:$("newPlCat").value.trim()||"Other",
          emoji:$("newPlEmoji").value||"🎬", desc:$("newPlDesc").value.trim(), thumb:pendingThumb||"" };
      } else body.playlist_id=Number($("addPlaylist").value);
      const r=await api("/api/add",{json:body});
      toast(r.warning ? "⚠️ "+r.warning : "✅ Episode add ho gaya!");
      $("addModal").classList.add("hidden");
      $("addLink480").value=""; $("addLink720").value=""; $("addLink1080").value=""; $("addEpTitle").value="";
      $("addSeason").value=1; $("addEpNum").value=1;
      await refresh();
      goWatch(r.playlist_id, body.season, body.ep_num);
    }catch(e){
      const msg=e.message||"Add fail hua";
      if(msg.toLowerCase().includes("link")||msg.toLowerCase().includes("channel")) $("linkErr").textContent=msg;
      else $("addErr").textContent=msg;
    }finally{ btn.disabled=false; btn.textContent="Add to Playlist"; }
  };

  // ---------- init ----------
  document.addEventListener("keydown",e=>{ if(e.key==="Escape"){ $("signinModal").classList.add("hidden"); $("addModal").classList.add("hidden"); } });
  window.addEventListener("beforeunload",saveProgress);
  if("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(()=>{});

  (async ()=>{
    await loadConfig();
    await loadLib();
    renderHome();
    setInterval(refresh, 30000);
  })();
})();
