document.addEventListener("DOMContentLoaded", () => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  /* ---------- mobile navigation ---------- */
  const menuToggle = $(".menu-toggle");
  const nav = $(".nav-links");
  if (menuToggle && nav) {
    const setOpen = open => {
      nav.classList.toggle("nav-open", open);
      menuToggle.setAttribute("aria-expanded", String(open));
      document.body.classList.toggle("menu-is-open", open);
    };
    menuToggle.addEventListener("click", () => setOpen(!nav.classList.contains("nav-open")));
    $$("a", nav).forEach(link => link.addEventListener("click", () => setOpen(false)));
    document.addEventListener("keydown", e => { if (e.key === "Escape") setOpen(false); });
  }

  /* ---------- live server status (every widget on the page) ---------- */
  const statusTexts = $$("[data-status-text]");
  const statusDots = $$("[data-status-dot]");
  const STATES = {
    online: ["Online", "status-online"],
    offline: ["Offline", "status-offline"],
    maintenance: ["Maintenance", "status-maintenance"],
    prelaunch: ["Opens at launch", "status-prelaunch"],
    unknown: ["Status unavailable", "status-unknown"],
  };
  function paintStatus(state, data) {
    const [label, cls] = STATES[state] || STATES.unknown;
    let text = label;
    if (state === "online") {
      if (Number.isInteger(data.players)) text += ` · ${data.players}${Number.isInteger(data.max) ? "/" + data.max : ""} players`;
    }
    document.documentElement.dataset.serverState = STATES[state] ? state : "unknown";
    statusTexts.forEach(el => { el.textContent = text; });
    statusDots.forEach(el => { el.className = "status-dot " + cls; el.title = text; });
  }
  async function refreshStatus() {
    if (!statusTexts.length) return;
    try {
      const response = await fetch("/api/server-status?ts=" + Date.now(), { cache: "no-store", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("bad status");
      const data = await response.json();
      paintStatus(data.state, data);
    } catch (_) { paintStatus("unknown", {}); }
  }
  if (statusTexts.length) {
    refreshStatus();
    setInterval(() => { if (!document.hidden) refreshStatus(); }, 60000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshStatus(); });
  }

  /* ---------- launch countdown (home card + dashboard strip) ---------- */
  const launchNodes = $$("[data-launch-ts]");
  if (launchNodes.length) {
    const target = Number(launchNodes[0].dataset.launchTs);
    const pad = n => String(Math.max(0, n)).padStart(2, "0");
    let reloaded = false;
    const tick = () => {
      const left = target - Date.now();
      const s = Math.max(0, Math.floor(left / 1000));
      const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60), sec = s % 60;
      $$("[data-cd]").forEach(el => { el.textContent = ({ days: pad(d), hours: pad(h), minutes: pad(m), seconds: pad(sec) })[el.dataset.cd]; });
      $$("[data-countdown-inline]").forEach(el => { el.textContent = `${d}d ${pad(h)}h ${pad(m)}m ${pad(sec)}s`; });
      if (left <= 0) {
        $$("[data-launch-ts]").forEach(el => el.classList.add("is-live"));
        // The address unlocks server-side at launch; reload once to show it.
        if (!reloaded && !sessionStorage.getItem("freedom-launch-reload")) {
          reloaded = true;
          try { sessionStorage.setItem("freedom-launch-reload", "1"); } catch (_) {}
          setTimeout(() => location.reload(), 1500);
        }
        return false;
      }
      return true;
    };
    if (tick()) { const timer = setInterval(() => { if (!tick()) clearInterval(timer); }, 1000); }
  }

  /* ---------- copy buttons ---------- */
  async function copyText(button, value) {
    try {
      await navigator.clipboard.writeText(value);
      const old = button.textContent; button.textContent = "Copied!";
      setTimeout(() => { button.textContent = old; }, 1800);
    } catch (_) { window.prompt("Copy this value:", value); }
  }
  $$("[data-copy-ip], [data-copy-text]").forEach(button => {
    button.addEventListener("click", () => copyText(button, button.dataset.copyIp || button.dataset.copyText || ""));
  });
  $$("[data-copy-from]").forEach(button => {
    button.addEventListener("click", () => { const src = $(button.dataset.copyFrom); if (src) copyText(button, src.value); });
  });

  /* ---------- gallery lightbox ---------- */
  $$("[data-lightbox]").forEach(link => link.addEventListener("click", e => {
    e.preventDefault();
    const box = document.createElement("div");
    box.className = "lightbox"; box.setAttribute("role", "dialog"); box.setAttribute("aria-label", link.dataset.lightbox);
    const img = document.createElement("img"); img.src = link.href; img.alt = link.querySelector("img")?.alt || "";
    const cap = document.createElement("p"); cap.textContent = link.dataset.lightbox + "  ·  click or press Esc to close";
    box.append(img, cap);
    const close = () => { box.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = ev => { if (ev.key === "Escape") close(); };
    box.addEventListener("click", close); document.addEventListener("keydown", onKey);
    document.body.appendChild(box);
  }));

  /* ---------- flash messages ---------- */
  $$(".flash").forEach(flash => {
    $(".flash-close", flash)?.addEventListener("click", () => flash.remove());
    if (!flash.classList.contains("flash-sticky")) setTimeout(() => flash.remove(), 9000);
  });

  /* ---------- reveal on scroll ---------- */
  const revealNodes = $$(".reveal");
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => { if (entry.isIntersecting) { entry.target.classList.add("is-visible"); observer.unobserve(entry.target); } });
    }, { threshold: 0.12 });
    revealNodes.forEach(node => observer.observe(node));
  } else revealNodes.forEach(node => node.classList.add("is-visible"));

  /* ---------- forms: password helpers, double-submit guard, confirms ---------- */
  $$("[data-pw-toggle]").forEach(btn => btn.addEventListener("click", () => {
    const input = $("input", btn.parentElement);
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.textContent = show ? "Hide" : "Show";
    btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
  }));

  function strength(pw) {
    let score = 0;
    if (pw.length >= 10) score++;
    if (pw.length >= 14) score++;
    if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score++;
    if (/\d/.test(pw) && /[^A-Za-z0-9]/.test(pw)) score++;
    return pw.length < 10 ? Math.min(score, 1) : Math.max(score, 1);
  }
  $$("[data-pw-strength]").forEach(input => {
    const scope = input.closest("label");
    const meter = $("[data-pw-meter]", scope);
    const hint = $("[data-pw-hint]", scope);
    const words = ["", "Weak", "Okay", "Good", "Strong"];
    if (hint) hint.dataset.default = hint.textContent;
    input.addEventListener("input", () => {
      const pw = input.value;
      const s = pw ? strength(pw) : 0;
      if (meter) meter.dataset.score = String(s);
      if (hint) {
        hint.textContent = pw ? (pw.length < 10 ? `${10 - pw.length} more character(s) needed` : words[s] + " password") : hint.dataset.default || hint.textContent;
        hint.className = "field-hint" + (pw ? (pw.length < 10 ? " is-bad" : " is-good") : "");
      }
    });
  });
  $$("[data-pw-confirm]").forEach(input => {
    const form = input.closest("form");
    const main = $("[data-pw-strength]", form);
    const hint = $("[data-confirm-hint]", input.closest("label"));
    const check = () => {
      if (!hint || !main) return;
      if (!input.value) { hint.textContent = ""; return; }
      const same = input.value === main.value;
      hint.textContent = same ? "Passwords match" : "Passwords don't match yet";
      hint.className = "field-hint " + (same ? "is-good" : "is-bad");
    };
    input.addEventListener("input", check);
    main?.addEventListener("input", check);
  });

  $$("form[data-auth-form]").forEach(form => {
    form.addEventListener("submit", e => {
      if (!form.checkValidity()) { return; }
      const btn = $("button[type=submit]", form);
      if (btn && !btn.disabled) { setTimeout(() => { btn.disabled = true; btn.dataset.label = btn.textContent; btn.textContent = "Please wait…"; }, 0); }
    });
    // let the browser show its own messages, but scroll to the first problem on mobile
    form.addEventListener("invalid", e => e.target.scrollIntoView({ block: "center", behavior: "smooth" }), true);
  });
  window.addEventListener("pageshow", e => {
    if (e.persisted) $$("form[data-auth-form] button[type=submit]").forEach(b => { b.disabled = false; if (b.dataset.label) b.textContent = b.dataset.label; });
  });

  $$("form[data-confirm]").forEach(form => form.addEventListener("submit", e => {
    if (!window.confirm(form.dataset.confirm)) e.preventDefault();
  }));

  /* ---------- text counters ---------- */
  $$("textarea[maxlength]").forEach(area => {
    const counter = area.closest("label")?.querySelector("[data-count-for]");
    const update = () => { if (counter) counter.textContent = `${area.value.length} / ${area.maxLength}`; };
    area.addEventListener("input", update); update();
  });

  /* ---------- support form hints ---------- */
  const category = $("[data-ticket-category]");
  const ticketHint = $("[data-ticket-hint]");
  if (category && ticketHint) {
    const hints = {
      password: "Use the email on your account. Staff verify it and send a temporary password.",
      ban_appeal: "Explain what happened and why the decision should be reviewed. Be honest and respectful.",
      rank_payment: "Include your Minecraft name, the rank, and the transaction hash.",
      whitelist: "Include the Minecraft username you requested.",
      bug: "Include your Minecraft name, what happened, when, and the player involved if any.",
    };
    const show = () => { ticketHint.textContent = hints[category.value] || ""; };
    category.addEventListener("change", show); show();
  }

  /* ---------- admin: tabs, filters ---------- */
  const admin = $("[data-admin]");
  if (admin) {
    admin.classList.add("js-tabs");
    const links = $$("[data-tab-link]", admin);
    const panels = $$("[data-tab]", admin);
    const names = panels.map(p => p.dataset.tab);
    const activate = name => {
      if (!names.includes(name)) name = "overview";
      panels.forEach(p => p.classList.toggle("is-active", p.dataset.tab === name));
      links.forEach(l => l.classList.toggle("is-active", l.dataset.tabLink === name));
    };
    const fromHash = () => activate(decodeURIComponent(location.hash.slice(1)));
    window.addEventListener("hashchange", fromHash);
    fromHash();
    // overview stat cards link to other tabs via hash; make sure the page doesn't jump
    links.forEach(l => l.addEventListener("click", () => window.scrollTo({ top: 0 })));

    $$("[data-filter-input]").forEach(input => {
      const items = () => $$(input.dataset.filterInput);
      const chips = input.parentElement.querySelector("[data-filter-chips]");
      const apply = () => {
        const q = input.value.trim().toLowerCase();
        const status = chips ? ($(".chip.is-on", chips)?.dataset.status || "all") : "all";
        items().forEach(item => {
          const okText = !q || item.textContent.toLowerCase().includes(q);
          const okStatus = status === "all" || item.dataset.status === status;
          item.style.display = okText && okStatus ? "" : "none";
        });
      };
      input.addEventListener("input", apply);
      chips?.addEventListener("click", e => {
        const chip = e.target.closest(".chip"); if (!chip) return;
        $$(".chip", chips).forEach(c => c.classList.toggle("is-on", c === chip));
        apply();
      });
      // default to showing the pending queue first if there is any
      const firstPending = chips && items().some(i => i.dataset.status === "pending" || i.dataset.status === "open");
      if (firstPending) {
        const wanted = items().some(i => i.dataset.status === "pending") ? "pending" : "open";
        const chip = $(`.chip[data-status="${wanted}"]`, chips);
        if (chip) chip.click();
      }
    });
  }
});
