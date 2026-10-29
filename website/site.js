"use strict";
// Everything here runs inside this page. It never requests a camera or
// microphone and cannot move the real cursor.

(function dashboardShot() {
  const figure = document.getElementById("dashboard-shot");
  const image = document.getElementById("dashboard-img");
  if (!figure || !image) return;
  const missing = () => figure.classList.add("missing");
  image.addEventListener("error", missing);
  if (image.complete && image.naturalWidth === 0) missing();
})();

(function practicePad() {
  const stage = document.getElementById("pad-stage");
  if (!stage) return;
  const pointer = document.getElementById("pad-pointer");
  const overlayText = document.getElementById("pad-overlay-text");
  const menu = document.getElementById("pad-menu");
  const windowBox = stage.querySelector(".pad-window");
  const targets = [...stage.querySelectorAll(".pad-target")];
  const handBox = document.getElementById("pad-hand-box");
  const handUse = document.getElementById("pad-hand-use");
  const handTitle = document.getElementById("pad-hand-title");
  const handLabel = document.getElementById("pad-hand-label");
  const progress = [...document.querySelectorAll("#pad-progress li")];
  const buttons = Object.fromEntries([...document.querySelectorAll("[data-pad]")].map((b) => [b.dataset.pad, b]));
  const guard = document.getElementById("pad-guard");
  const status = document.getElementById("pad-status");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  const COOLDOWN_MS = 600;

  let state = "off"; // off, looking, tracking, stopped
  let guardArmed = false;
  let pos = {x: 0.5, y: 0.5};
  let lastClick = -Infinity;
  let hovered = null;
  let selected = null;
  let poseTimer = 0;
  let clapTimer = 0;
  let pinched = false;

  const say = (text) => { status.textContent = text; };
  const running = () => state === "looking" || state === "tracking";

  function setStep(step) {
    progress.forEach((item, index) => {
      const n = index + 1;
      item.classList.toggle("done", n < step);
      if (n === step) item.setAttribute("aria-current", "step"); else item.removeAttribute("aria-current");
    });
    const next = {1: "watch", 2: "open", 3: "left", 4: "clap"}[step];
    for (const [name, button] of Object.entries(buttons)) button.classList.toggle("next", name === next);
  }

  function setHand(mode, pose, label) {
    window.clearTimeout(poseTimer);
    handBox.dataset.mode = mode;
    handUse.setAttribute("href", pose === "left" ? "#g-hand-left-click" : pose === "right" ? "#g-hand-right-click" : "#g-hand-open");
    handLabel.textContent = label;
    handTitle.textContent = "Practice hand: " + label.toLowerCase();
  }

  function stageRect() { return stage.getBoundingClientRect(); }

  function targetAt() {
    const rect = stageRect();
    const x = rect.left + pos.x * rect.width;
    const y = rect.top + pos.y * rect.height;
    const inside = (el) => { const r = el.getBoundingClientRect(); return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom; };
    if (!menu.hidden) {
      const item = [...menu.children].find(inside);
      if (item) return {menuItem: item};
    }
    return {target: targets.find(inside) || null};
  }

  function placePointer() {
    pointer.style.left = (pos.x * 100) + "%";
    pointer.style.top = (pos.y * 100) + "%";
    const found = targetAt().target || null;
    if (found !== hovered) {
      if (hovered) hovered.classList.remove("hover");
      hovered = found;
      if (hovered) hovered.classList.add("hover");
    }
  }

  function moveTo(x, y) {
    if (state !== "tracking") return;
    pos = {x: Math.min(0.98, Math.max(0.02, x)), y: Math.min(0.96, Math.max(0.03, y))};
    placePointer();
  }

  function clearDesk() {
    menu.hidden = true;
    targets.forEach((t) => t.classList.remove("selected", "opened", "hover"));
    hovered = null;
    selected = null;
    pinched = false;
  }

  function stopControl(reason) {
    state = "stopped";
    stage.classList.remove("tracking");
    stage.classList.add("stopped");
    menu.hidden = true;
    setStep(5);
    buttons.watch.classList.add("next");
    overlayText.textContent = "Hand control stopped. Say “watch me” to start again.";
    const guardText = guardArmed ? "armed, exactly as you left it." : "not armed, exactly as you left it.";
    say(reason + " Hand control stopped and the practice pointer is frozen. The presence guard is still " + guardText);
  }

  function watch() {
    if (running()) { say("Already watching. Show an open hand, or clap to stop."); return; }
    if (guardArmed) {
      say("Busy: the presence guard is using the camera. Jarvis tells you this instead of turning the guard off. Disarm the guard first if you want hand control.");
      return;
    }
    clearDesk();
    state = "looking";
    stage.classList.remove("stopped", "tracking");
    setHand("looking", "open", "Looking for a hand");
    setStep(2);
    overlayText.textContent = "Step 2: show an open hand.";
    say("In the desktop app, Jarvis would now turn on the camera for hand control only. Next: show an open hand.");
  }

  function openHand() {
    if (state === "tracking") { say("Your hand is already found. Steer, pinch, or clap to stop."); return; }
    if (state !== "looking") { say("Start with step 1: say “watch me”."); return; }
    state = "tracking";
    pos = {x: 0.5, y: 0.5};
    stage.classList.add("tracking");
    setHand("tracking", "open", "Tracking index finger");
    placePointer();
    setStep(3);
    say("Hand found. Steer with your mouse or finger inside the practice area, or click it and use the arrow keys. Point at a file, then pinch.");
  }

  function pinch(button) {
    if (state !== "tracking") {
      say(state === "looking" ? "Show an open hand first so the tracker can find your fingers." : "Start with step 1: say “watch me”. Clicks only work while hand control is running.");
      return;
    }
    const now = performance.now();
    if (now - lastClick < COOLDOWN_MS) { say("One click per pinch. Release, then pinch again."); return; }
    lastClick = now;
    const left = button === "left";
    setHand("tracking", button, left ? "Thumb and middle: left click" : "Thumb and ring: right click");
    pointer.classList.add(left ? "click" : "click-right");
    poseTimer = window.setTimeout(() => {
      pointer.classList.remove("click", "click-right");
      if (state === "tracking") setHand("tracking", "open", "Tracking index finger");
    }, reduced.matches ? 250 : COOLDOWN_MS);

    const hit = targetAt();
    if (left) {
      if (hit.menuItem) {
        menu.hidden = true;
        const name = selected ? selected.dataset.target : "the desktop";
        if (selected && hit.menuItem.textContent === "Open") selected.classList.add("opened");
        say("Left click on “" + hit.menuItem.textContent + "” for " + name + ". In practice only, nothing on your computer changed.");
      } else if (hit.target) {
        menu.hidden = true;
        targets.forEach((t) => t.classList.remove("selected", "opened"));
        hit.target.classList.add("selected");
        selected = hit.target;
        say("Left click: " + hit.target.dataset.target + " is selected. Now try a right click with thumb and ring finger.");
      } else {
        menu.hidden = true;
        targets.forEach((t) => t.classList.remove("selected"));
        selected = null;
        say("Left click on empty space. Steer the pointer onto a file first.");
      }
    } else {
      targets.forEach((t) => t.classList.remove("selected"));
      selected = hit.target || null;
      if (selected) selected.classList.add("selected");
      const win = windowBox.getBoundingClientRect();
      const rect = stageRect();
      menu.hidden = false;
      const mx = rect.left + pos.x * rect.width - win.left + 4;
      const my = rect.top + pos.y * rect.height - win.top + 4;
      menu.style.left = Math.max(4, Math.min(mx, win.width - menu.offsetWidth - 4)) + "px";
      menu.style.top = Math.max(4, Math.min(my, win.height - menu.offsetHeight - 4)) + "px";
      say("Right click" + (selected ? " on " + selected.dataset.target : " on the desktop") + ": a menu opened. Steer onto “Open” and left click it.");
    }
    if (!pinched) { pinched = true; setStep(4); }
  }

  function clap() {
    if (!running()) { say("Hand control is not running, so there is nothing to stop. Say “watch me” to start."); return; }
    window.clearTimeout(clapTimer);
    stopControl("Clap seen: hands apart, then together.");
    setHand("clap", "open", "Clap seen");
    handBox.dataset.clap = "apart";
    clapTimer = window.setTimeout(() => { handBox.dataset.clap = "together"; }, reduced.matches ? 0 : 450);
  }

  function escape() {
    if (!running()) { say("Hand control is not running. Esc stops it any time it is."); return; }
    stopControl("Esc pressed.");
    setHand("off", "open", "Stopped by Esc");
  }

  function toggleGuard() {
    if (running()) { say("Stop hand control first. In the desktop app, each camera has one owner at a time."); return; }
    guardArmed = !guardArmed;
    guard.setAttribute("aria-pressed", String(guardArmed));
    guard.textContent = guardArmed ? "Armed" : "Not armed";
    say(guardArmed ? "Practice presence guard armed with its own switch. A clap or Esc will not disarm it." : "Practice presence guard disarmed. Only this switch changes it.");
  }

  buttons.watch.addEventListener("click", watch);
  buttons.open.addEventListener("click", openHand);
  buttons.left.addEventListener("click", () => pinch("left"));
  buttons.right.addEventListener("click", () => pinch("right"));
  buttons.clap.addEventListener("click", clap);
  buttons.esc.addEventListener("click", escape);
  guard.addEventListener("click", toggleGuard);

  const fromEvent = (event) => {
    const rect = stageRect();
    moveTo((event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height);
  };
  stage.addEventListener("pointermove", fromEvent);
  stage.addEventListener("pointerdown", fromEvent);

  stage.addEventListener("keydown", (event) => {
    if (state !== "tracking") return;
    const step = event.shiftKey ? 0.1 : 0.04;
    const moves = {ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step]};
    const move = moves[event.key];
    if (!move) return;
    event.preventDefault();
    moveTo(pos.x + move[0], pos.y + move[1]);
    if (hovered) say("Pointer over " + hovered.dataset.target + ".");
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && running()) escape();
  });

  setHand("off", "open", "Camera off");
  setStep(1);
})();
