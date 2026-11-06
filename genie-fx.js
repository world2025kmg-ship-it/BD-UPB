(function () {
  const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const $ = s => document.querySelector(s);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const rnd = (a, b) => a + Math.random() * (b - a);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pointer = { nx: 0, ny: 0 };
  let lastMove = Date.now(), lastAct = Date.now(), lastSpark = [0, 0];

  function gear(n, ro, ri) {
    const st = Math.PI * 2 / n, p = [];
    for (let t = 0; t < n; t++)
      [[0, ri], [.15, ro], [.5, ro], [.65, ri]].forEach(([k, r]) => {
        const a = (t + k) * st; p.push((r * Math.cos(a)).toFixed(1) + ',' + (r * Math.sin(a)).toFixed(1));
      });
    return 'M' + p.join('L') + 'Z';
  }
  function leaf(c, s) { c.beginPath(); c.moveTo(0, -s); c.quadraticCurveTo(s * .75, 0, 0, s); c.quadraticCurveTo(-s * .75, 0, 0, -s); c.fill(); }
  function star(c, s) { c.beginPath(); for (let i = 0; i < 8; i++) { const r = i % 2 ? s * .3 : s, a = i * Math.PI / 4; c.lineTo(Math.cos(a) * r, Math.sin(a) * r); } c.fill(); }


  const anims = [], gl = document.createElement('div');
  gl.id = 'fx-gears'; document.body.prepend(gl);
  [[420, 14, 'left:-120px;bottom:-110px', 70, 1], [280, 10, 'right:-70px;top:90px', 48, -1], [150, 8, 'left:10%;top:22%', 26, -1]].forEach(([s, t, css, d, k]) => {
    const v = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    v.setAttribute('viewBox', '-100 -100 200 200'); v.setAttribute('width', s); v.setAttribute('height', s); v.style.cssText = css;
    v.innerHTML = `<path d="${gear(t, 92, 76)}"/><circle r="30"/><circle r="54" stroke-dasharray="4 9"/>`;
    gl.append(v);
    if (!RM) anims.push(v.animate([{ transform: 'rotate(0)' }, { transform: 'rotate(' + 360 * k + 'deg)' }], { duration: d * 1000, iterations: Infinity }));
  });
  let rate = 1, target = 1, raf;
  function tick() { rate += (target - rate) * .07; anims.forEach(a => a.updatePlaybackRate(rate)); if (Math.abs(target - rate) > .02) raf = requestAnimationFrame(tick); }
  const busy = on => { target = on ? 9 : 1; cancelAnimationFrame(raf); tick(); };

  const lc = document.createElement('canvas'); lc.id = 'fx-leaves'; document.body.prepend(lc);
  const lx = lc.getContext('2d'); let W, H;
  const size = () => { W = lc.width = innerWidth; H = lc.height = innerHeight; };
  size(); addEventListener('resize', size);
  const mk = () => ({ x: rnd(0, W), y: rnd(0, H), s: rnd(6, 17), vy: rnd(.2, .6), ph: rnd(0, 6.3), sw: rnd(.4, 1), r: rnd(0, 6.3), vr: rnd(-.01, .01), a: rnd(.55, .95), d: rnd(.35, 1) });
  const leaves = Array.from({ length: RM ? 0 : innerWidth < 700 ? 14 : 30 }, mk);
  (function frame(t) {
    lx.clearRect(0, 0, W, H);
    const k = rate > 1.5 ? 1 + rate * .35 : 1;
    leaves.forEach(l => {
      l.y -= l.vy * l.d * k; l.x += Math.sin(t / 1000 * l.sw + l.ph) * .4 * k; l.r += l.vr * k;
      if (l.y < -30) { l.y = H + 30; l.x = rnd(0, W); }
      lx.save(); lx.translate(l.x + pointer.nx * l.d * 28, l.y + pointer.ny * l.d * 18); lx.rotate(l.r);
      lx.globalAlpha = l.a * l.d; lx.fillStyle = l.d > .65 ? '#ffd566' : '#f9b91f'; leaf(lx, l.s * (.6 + l.d * .6)); lx.restore();
    });
    requestAnimationFrame(frame);
  })(0);

  const bits = []; let tc, tx, running = false;
  function runBits() {
    if (!tc) { tc = document.createElement('canvas'); tc.id = 'fx-confetti'; document.body.append(tc); tx = tc.getContext('2d'); }
    if (running) return; running = true; tc.width = innerWidth; tc.height = innerHeight;
    (function loop() {
      tx.clearRect(0, 0, tc.width, tc.height);
      for (let i = bits.length - 1; i >= 0; i--) {
        const p = bits[i];
        if (--p.life <= 0) { bits.splice(i, 1); continue; }
        p.vy += p.g; p.vx *= p.fr; p.x += p.vx; p.y += p.vy; p.a += p.va;
        tx.save(); tx.translate(p.x, p.y); tx.rotate(p.a); tx.globalAlpha = Math.min(1, p.life / p.max * 1.6); tx.fillStyle = p.c;
        p.t === 'l' ? leaf(tx, p.w) : p.t === 's' ? star(tx, p.w) : tx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
        tx.restore();
      }
      bits.length ? requestAnimationFrame(loop) : (running = false, tx.clearRect(0, 0, tc.width, tc.height));
    })();
  }
  function confetti() {
    if (RM) return;
    const col = ['#f9b91f', '#ffd566', '#ffffff', '#bcd8f6', '#3b78f7'];
    for (let i = 0; i < 140; i++) {
      const life = 150 + Math.random() * 60 | 0;
      bits.push({ t: Math.random() < .55 ? 'l' : 'r', x: innerWidth / 2, y: innerHeight * .45, vx: rnd(-8, 8), vy: rnd(-17, -4), g: .35, fr: .99, w: rnd(6, 13), h: rnd(4, 9), a: rnd(0, 6), va: rnd(-.25, .25), c: col[Math.random() * 5 | 0], life, max: life });
    }
    runBits();
  }


  const BOT = `<div class="bot3d" id="bot" data-mood="happy" title="Cliquez sur moi !"><div class="bot-shadow"></div>
<div class="bot-act"><div class="bot-float"><div class="bot-in">
<svg viewBox="0 0 300 340" aria-hidden="true"><defs>
<radialGradient id="gH" cx=".32" cy=".25" r=".9"><stop offset="0" stop-color="#f4faff"/><stop offset=".45" stop-color="#bcd8f6"/><stop offset="1" stop-color="#7fa9e0"/></radialGradient>
<linearGradient id="gB" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#d8e8fb"/><stop offset="1" stop-color="#86b0e4"/></linearGradient>
<radialGradient id="gV" cx=".38" cy=".2" r=".95"><stop offset="0" stop-color="#2d3d78"/><stop offset=".6" stop-color="#0d1536"/><stop offset="1" stop-color="#050a1e"/></radialGradient>
<radialGradient id="gBall" cx=".35" cy=".3" r=".8"><stop offset="0" stop-color="#fff"/><stop offset=".6" stop-color="#bcd8f6"/><stop offset="1" stop-color="#7fa9e0"/></radialGradient>
<linearGradient id="gGl" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".7"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient></defs>
<g id="body"><path d="M104 214C94 268 112 306 150 306C188 306 206 268 196 214Z" fill="url(#gB)" stroke="#fff" stroke-width="4" stroke-linejoin="round"/>
<path d="M112 238C108 262 118 284 134 294" stroke="#fff" stroke-opacity=".6" stroke-width="6" fill="none" stroke-linecap="round"/>
<rect x="128" y="252" width="44" height="38" rx="6" fill="#fff"/><text x="150" y="277" text-anchor="middle" font-family="Bricolage Grotesque,Arial" font-weight="800" font-size="15" fill="#1b2275">UPB</text>
<ellipse cx="124" cy="306" rx="22" ry="10" fill="url(#gB)" stroke="#fff" stroke-width="4"/><ellipse cx="176" cy="306" rx="22" ry="10" fill="url(#gB)" stroke="#fff" stroke-width="4"/></g>
<g id="aR" class="arm"><ellipse cx="224" cy="262" rx="17" ry="28" transform="rotate(-32 224 262)" fill="url(#gB)" stroke="#fff" stroke-width="4"/></g>
<g id="aL" class="arm"><ellipse cx="81" cy="210" rx="17" ry="36" transform="rotate(-39 81 210)" fill="url(#gB)" stroke="#fff" stroke-width="4"/>
<circle cx="58" cy="181" r="17" fill="url(#gB)" stroke="#fff" stroke-width="4"/></g>
<g id="ant"><rect x="146" y="22" width="8" height="32" rx="4" fill="url(#gB)" stroke="#fff" stroke-width="3"/><circle cx="150" cy="18" r="16" fill="url(#gBall)" stroke="#fff" stroke-width="4"/><circle id="bulb" cx="150" cy="18" r="8"/></g>
<g id="head"><ellipse cx="52" cy="132" rx="24" ry="32" fill="url(#gB)" stroke="#fff" stroke-width="4"/><ellipse cx="248" cy="132" rx="24" ry="32" fill="url(#gB)" stroke="#fff" stroke-width="4"/>
<ellipse cx="150" cy="128" rx="104" ry="90" fill="url(#gH)" stroke="#fff" stroke-width="5"/>
<ellipse cx="150" cy="132" rx="78" ry="60" fill="url(#gV)" stroke="#9cc3f0" stroke-opacity=".5" stroke-width="3"/>
<g id="face"><g id="eyes">
<g class="eL"><path class="wk" d="M106 124Q120 102 136 122" stroke="#fff" stroke-width="9" fill="none" stroke-linecap="round"/><circle class="op" cx="121" cy="120" r="14" fill="#fff"/><path class="cl" d="M108 122Q121 132 134 122" stroke="#fff" stroke-width="7" fill="none" stroke-linecap="round"/></g>
<g class="eR"><circle class="op" cx="184" cy="118" r="15" fill="#fff"/><path class="wk" d="M170 122Q184 100 200 120" stroke="#fff" stroke-width="9" fill="none" stroke-linecap="round"/><path class="cl" d="M170 120Q184 130 198 120" stroke="#fff" stroke-width="7" fill="none" stroke-linecap="round"/></g></g>
<path class="m-happy" d="M131 150Q150 170 172 150" stroke="#fff" stroke-width="7" fill="none" stroke-linecap="round"/>
<path class="m-party" d="M128 146Q150 186 174 146Z" fill="#fff"/>
<path class="m-sad" d="M133 166Q150 148 169 166" stroke="#fff" stroke-width="7" fill="none" stroke-linecap="round"/>
<ellipse class="m-think" cx="150" cy="158" rx="7" ry="5" stroke="#fff" stroke-width="6" fill="none"/>
<ellipse class="m-wow" cx="150" cy="160" rx="10" ry="13" fill="#fff"/>
<path class="m-sleep" d="M140 158H160" stroke="#fff" stroke-width="6" stroke-linecap="round"/>
<circle cx="102" cy="150" r="9" fill="#ff8fb0" opacity=".28"/><circle cx="198" cy="150" r="9" fill="#ff8fb0" opacity=".28"/></g>
<ellipse cx="118" cy="98" rx="38" ry="11" transform="rotate(-18 118 98)" fill="url(#gGl)"/>
<ellipse cx="96" cy="64" rx="46" ry="14" transform="rotate(-28 96 64)" fill="#fff" opacity=".45"/></g>
<g class="fx fx-think"><circle cx="222" cy="66" r="4"/><circle cx="242" cy="46" r="6"/><circle cx="266" cy="20" r="9"/></g>
<g class="fx fx-zzz"><text x="222" y="60">z</text><text x="244" y="38">Z</text></g>
<g class="fx fx-star"><text x="12" y="70">✦</text><text x="266" y="96">✦</text><text x="250" y="20">✦</text></g>
</svg></div></div></div></div>`;

  let bot, cur = { x: 0, y: 0 }, tgt = { x: 0, y: 0 }, sleeping = false, tm, moodTm;
  const retrig = (c, ms) => { if (!bot) return; bot.classList.remove(c); void bot.offsetWidth; bot.classList.add(c); setTimeout(() => bot.classList.remove(c), ms); };
  const jump = () => retrig('jump', 1000);
  const nod = () => retrig('nod', 300);
  const wave = () => retrig('waving', 3200);
  function mood(m) {
    if (!bot) return; bot.dataset.mood = m; clearTimeout(moodTm);
    if (m === 'celebrate') { jump(); moodTm = setTimeout(() => mood('happy'), 5000); }
  }
  function say(t) {
    const el = $('#say'); clearInterval(tm);
    if (!el) return;
    if (RM) { el.textContent = t; return; }
    el.textContent = ''; bot && bot.classList.add('talking');
    let i = 0;
    tm = setInterval(() => { el.textContent = t.slice(0, ++i); if (i >= t.length) { clearInterval(tm); bot && bot.classList.remove('talking'); } }, 22);
  }
  const pokeLines = ["Aïe, ça chatouille !", "Hihi ! Je suis le petit génie de l'UPB.", "Besoin d'un coup de main ? Je suis là.", "Vous avez de bons réflexes, vous !"];
  function wake() {
    lastAct = Date.now();
    if (sleeping) { sleeping = false; mood('happy'); wave(); say("Ah, vous revoilà ! Je suis prêt."); }
  }
  function mountBot(sel) {
    const el = $(sel); if (!el) return;
    el.innerHTML = BOT; bot = el.querySelector('.bot3d');
    bot.addEventListener('click', () => { wake(); jump(); say(pokeLines[Math.random() * pokeLines.length | 0]); });
    setTimeout(wave, 700);
    if (RM) return;
    setInterval(() => {
      if (Date.now() - lastAct > 28000 && !sleeping) { sleeping = true; mood('sleep'); say("Zzz... réveillez-moi quand vous voulez."); return; }
      if (sleeping || bot.dataset.mood !== 'happy') return;
      if (Math.random() < .6) wave(); else { lastMove = 0; tgt.x = Math.random() < .5 ? -1 : 1; tgt.y = .3; }
    }, 9000);
  }
  (function botLoop(t) {
    if (bot && !RM) {
      if (Date.now() - lastMove > 3500) { tgt.x = Math.sin(t / 2300) * .6; tgt.y = Math.sin(t / 3100) * .3; }
      cur.x += (tgt.x - cur.x) * .09; cur.y += (tgt.y - cur.y) * .09;
      const s = bot.style, f = v => v.toFixed(3);
      s.setProperty('--px', f(cur.x)); s.setProperty('--py', f(cur.y)); s.setProperty('--ex', f(cur.x)); s.setProperty('--ey', f(cur.y));
      s.setProperty('--ry', (cur.x * 16).toFixed(2) + 'deg'); s.setProperty('--rx', (-cur.y * 10).toFixed(2) + 'deg');
    }
    requestAnimationFrame(botLoop);
  })(0);

  addEventListener('pointermove', e => {
    lastMove = Date.now(); wake();
    pointer.nx = (e.clientX / innerWidth - .5) * 2; pointer.ny = (e.clientY / innerHeight - .5) * 2;
    document.body.style.setProperty('--mx', e.clientX + 60 + 'px'); document.body.style.setProperty('--my', e.clientY + 60 + 'px');
    if (bot) {
      const r = bot.getBoundingClientRect();
      tgt.x = clamp((e.clientX - (r.left + r.width / 2)) / (innerWidth * .45), -1, 1);
      tgt.y = clamp((e.clientY - (r.top + r.height * .4)) / (innerHeight * .45), -1, 1);
    }
    if (!RM && Math.hypot(e.clientX - lastSpark[0], e.clientY - lastSpark[1]) > 46) {
      lastSpark = [e.clientX, e.clientY];
      bits.push({ t: 's', x: e.clientX, y: e.clientY, vx: rnd(-.6, .6), vy: rnd(.2, 1.2), g: .03, fr: 1, w: rnd(4, 8), h: 0, a: 0, va: .08, c: '#ffd566', life: 36, max: 36 }); runBits();
    }
  });
  addEventListener('keydown', wake); addEventListener('pointerdown', wake);


  document.querySelectorAll('[data-tilt]').forEach(el => {
    if (RM) return;
    el.addEventListener('pointermove', e => {
      const r = el.getBoundingClientRect(), x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
      el.style.setProperty('--gx', x * 100 + '%'); el.style.setProperty('--gy', y * 100 + '%');
      if (el.contains(document.activeElement) && document.activeElement.tagName === 'INPUT') return;
      el.style.setProperty('--ty', ((x - .5) * 5).toFixed(2) + 'deg'); el.style.setProperty('--tx', ((.5 - y) * 4).toFixed(2) + 'deg');
    });
    el.addEventListener('pointerleave', () => { el.style.setProperty('--tx', '0deg'); el.style.setProperty('--ty', '0deg'); });
  });

  function reveal(el, t) {
    if (RM) { el.textContent = t; return; }
    const ch = '0123456789ABCDEF-'; let i = 0;
    const id = setInterval(() => {
      el.textContent = t.split('').map((c, k) => k < i || c === '-' ? c : ch[Math.random() * ch.length | 0]).join('');
      if (i++ >= t.length) { clearInterval(id); el.textContent = t; }
    }, 70);
  }
  function countUp(el, to) {
    if (RM || !to) { el.textContent = to; return; }
    const t0 = performance.now();
    (function f(n) { const k = Math.min(1, (n - t0) / 1100); el.textContent = Math.round(to * (1 - Math.pow(1 - k, 3))); if (k < 1) requestAnimationFrame(f); })(t0);
  }

   function revealOnScroll(scope) {
    const root = scope || document;
    const els = Array.from(root.querySelectorAll('[data-reveal]:not(.revealed)'));
    if (!els.length) return;
    if (RM || !('IntersectionObserver' in window)) { els.forEach(el => el.classList.add('revealed')); return; }
    const io = new IntersectionObserver(entries => {
      entries.forEach(en => {
        if (!en.isIntersecting) return;
        en.target.classList.add('revealed');
        io.unobserve(en.target);
      });
    }, { threshold: .1, rootMargin: '0px 0px -6% 0px' });
    els.forEach(el => io.observe(el));
  }

   function magnetic(el, power) {
    if (!el || RM) return;
    const p = power == null ? 6 : power;
    el.addEventListener('pointermove', e => {
      const r = el.getBoundingClientRect();
      const x = (e.clientX - (r.left + r.width / 2)) / (r.width / 2);
      const y = (e.clientY - (r.top + r.height / 2)) / (r.height / 2);
      el.style.transform = 'translate(' + (x * p).toFixed(2) + 'px,' + (y * p * .7).toFixed(2) + 'px)';
    });
    el.addEventListener('pointerleave', () => { el.style.transform = ''; });
  }

   function bindRipples(selector) {
    if (RM) return;
    document.addEventListener('pointerdown', e => {
      const el = e.target.closest(selector);
      if (!el || el.disabled) return;
      const r = el.getBoundingClientRect();
      const d = Math.max(r.width, r.height) * 1.15;
      const s = document.createElement('span');
      s.className = 'fx-ripple';
      s.style.cssText = 'width:' + d + 'px;height:' + d + 'px;left:' + (e.clientX - r.left - d / 2) + 'px;top:' + (e.clientY - r.top - d / 2) + 'px';
      el.appendChild(s);
      setTimeout(() => s.remove(), 700);
    });
  }

  window.GFX = { say, mood, busy, confetti, reveal, countUp, esc, gear, mountBot, jump, nod, wave,
    revealOnScroll, magnetic, bindRipples, reduceMotion: RM };
})();
