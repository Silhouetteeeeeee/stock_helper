// 启动动画：短片 + 进度条 + 溶解进主界面。
// 覆盖层是 index.html 里的静态节点，本脚本只负责播放与退场，不依赖 Vue。
// 同一标签页只播一次；数据就绪后 0.6 秒开始 2 秒淡出，最长 8 秒兜底。
(function () {
  "use strict";

  var root = document.getElementById("boot");
  if (!root) return;

  var PLAYED_KEY = "bootPlayed";
  var reduced = false;
  try {
    reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch (error) { /* 旧浏览器不支持就当作没有偏好 */ }

  var alreadyPlayed = false;
  try {
    alreadyPlayed = sessionStorage.getItem(PLAYED_KEY) === "1";
  } catch (error) { /* 隐私模式禁用存储时照常播放 */ }

  if (alreadyPlayed || reduced) {
    root.remove();
    return;
  }
  try {
    sessionStorage.setItem(PLAYED_KEY, "1");
  } catch (error) { /* 记不住就下次再播，不影响本次 */ }

  var video = document.getElementById("boot-video");
  var fill = document.getElementById("boot-fill");
  var hint = document.getElementById("boot-hint");
  var sound = document.getElementById("boot-sound");
  var FADE_MS = 2000;
  var READY_DELAY_MS = 600;
  var HOLD_LIMIT_MS = 8000;
  var leaving = false;
  var ready = false;
  var progressStarted = performance.now();

  function showVideo() {
    video.classList.add("is-shown");
  }

  video.src = "/assets/boot/3.mp4";
  video.muted = true;
  var attempt = video.play();
  if (attempt && typeof attempt.catch === "function") {
    attempt.catch(function () {
      // 自动播放被拒时保留深色背景与进度条，不挡路
    });
  }
  video.addEventListener("playing", showVideo);
  video.addEventListener("loadeddata", showVideo);

  function setWidth(value) {
    fill.style.width = value.toFixed(1) + "%";
  }

  function tick() {
    if (leaving) return;
    var elapsed = performance.now() - progressStarted;
    if (ready) {
      setWidth(100);
      return;
    }
    setWidth(Math.min(85, 8 + (elapsed / 3000) * 77));
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  function enter() {
    if (leaving) return;
    leaving = true;
    setWidth(100);
    root.classList.add("boot-leaving");
    root.setAttribute("aria-hidden", "true");
    window.removeEventListener("keydown", onKey);
    setTimeout(function () {
      try {
        video.pause();
      } catch (error) { /* 已经卸载就算了 */ }
      video.removeAttribute("src");
      root.remove();
    }, FADE_MS + 120);
  }

  function markReady() {
    if (ready) return;
    ready = true;
    setWidth(100);
    setTimeout(enter, READY_DELAY_MS);
  }

  function onKey(event) {
    if (event.key === "Escape" || event.key === " " || event.key === "Enter") {
      event.preventDefault();
      enter();
    }
  }

  window.addEventListener("app-ready", markReady, { once: true });
  window.addEventListener("keydown", onKey);
  setTimeout(function () {
    if (ready) return;
    if (hint) hint.textContent = "应用启动较慢，正在进入…";
    markReady();
  }, HOLD_LIMIT_MS);

  function renderSound(refused) {
    if (!sound || !video) return;
    var on = !video.muted;
    sound.textContent = on ? "🔊" : "🔇";
    if (refused) {
      sound.title = "浏览器拒绝了声音，点一下重试";
      sound.setAttribute("aria-label", "重试打开影片声音");
    } else {
      sound.title = on ? "点一下关闭声音" : "点一下打开声音";
      sound.setAttribute("aria-label", on ? "关闭影片声音" : "打开影片声音");
    }
    if (hint) hint.textContent = on ? "点右下角关闭声音" : "点右下角打开声音";
  }

  if (sound && video) {
    sound.addEventListener("click", function (event) {
      event.stopPropagation();
      if (!video.muted) {
        video.muted = true;
        renderSound(false);
        return;
      }
      video.muted = false;
      video.volume = 0.65;
      var played = video.play();
      if (played && typeof played.catch === "function") {
        played.catch(function () {
          video.muted = true;
          renderSound(true);
        });
      }
      renderSound(false);
    });
  }
})();
