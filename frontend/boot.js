// 启动动画：短片 + 进度条 + 溶解进主界面。
// 覆盖层是 index.html 里的静态节点，本脚本只负责播放与退场，不依赖 Vue。
// 每次打开或刷新页面都会播放；短片放到最后 2 秒、应用也已就绪时开始溶解。
(function () {
  "use strict";

  var root = document.getElementById("boot");
  if (!root) return;

  var reduced = false;
  try {
    reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch (error) { /* 旧浏览器不支持就当作没有偏好 */ }

  if (reduced) {
    root.remove();
    return;
  }

  var video = document.getElementById("boot-video");
  var fill = document.getElementById("boot-fill");
  var hint = document.getElementById("boot-hint");
  var sound = document.getElementById("boot-sound");
  var FADE_MS = 2000;
  // 影片卡住或自动播放被拒时的兜底：装饰性画面不能永远挡着应用。
  var HOLD_LIMIT_MS = 12000;
  // 画面连续这么久不推进，就当作已经播完，免得网络卡在半路一直等下去。
  var STALL_LIMIT_MS = 5000;
  // 短于两次溶解时长的片子来不及做尾部溶解，只能等它自己放完。
  var TAIL_MIN_S = (FADE_MS * 2) / 1000;
  var leaving = false;
  var appReady = false;
  var clipDone = false;
  var progressStarted = performance.now();

  function showVideo() {
    video.classList.add("is-shown");
  }

  video.src = "/assets/boot/1.mp4";
  video.muted = true;
  var attempt = video.play();
  if (attempt && typeof attempt.catch === "function") {
    attempt.catch(function () {
      // 自动播放被拒时保留深色背景与进度条，不挡路；不再等这段片子。
      clipDone = true;
      maybeEnter();
    });
  }
  video.addEventListener("playing", showVideo);
  video.addEventListener("loadeddata", showVideo);

  function setWidth(value) {
    fill.style.width = value.toFixed(1) + "%";
  }

  function clipDuration() {
    var duration = video.duration;
    return isFinite(duration) && duration > 0 ? duration : 0;
  }

  function maybeEnter() {
    if (leaving || !appReady) return;
    var duration = clipDuration();
    if (clipDone) {
      enter();
      return;
    }
    if (duration < TAIL_MIN_S) return;
    if (video.currentTime >= duration - FADE_MS / 1000) enter();
  }

  function tick() {
    if (leaving) return;
    var duration = clipDuration();
    if (duration > 0) {
      // 进度条跟着片子走：片子放完时正好到顶。
      setWidth(Math.min(99, 8 + (video.currentTime / duration) * 91));
    } else {
      var elapsed = performance.now() - progressStarted;
      setWidth(Math.min(85, 8 + (elapsed / 3000) * 77));
    }
    maybeEnter();
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

  function onReady() {
    appReady = true;
    renderSound(false);
    maybeEnter();
  }

  function onKey(event) {
    if (event.key === "Escape" || event.key === " " || event.key === "Enter") {
      event.preventDefault();
      enter();
    }
  }

  video.addEventListener("ended", function () {
    clipDone = true;
    setWidth(100);
    if (!appReady && hint) hint.textContent = "应用启动较慢，就绪后自动进入";
    maybeEnter();
  });

  window.addEventListener("app-ready", onReady, { once: true });
  window.addEventListener("keydown", onKey);

  // 兜底：片子再长、网络再慢，也不能超过这个时间还挡着应用。
  setTimeout(function () {
    if (leaving) return;
    if (hint && !appReady) hint.textContent = "应用启动较慢，正在进入…";
    appReady = true;
    enter();
  }, HOLD_LIMIT_MS);

  // 片子停在半路（网络断流、解码卡住）时不再等它的结尾事件。
  var lastMediaTime = 0;
  var lastAdvanceAt = performance.now();
  setInterval(function () {
    if (leaving || clipDone) return;
    if (video.currentTime > lastMediaTime + 0.05) {
      lastMediaTime = video.currentTime;
      lastAdvanceAt = performance.now();
      return;
    }
    if (performance.now() - lastAdvanceAt >= STALL_LIMIT_MS) {
      clipDone = true;
      maybeEnter();
    }
  }, 1000);

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
