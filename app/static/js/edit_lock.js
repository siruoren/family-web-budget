/* 并发编辑锁 - 前端逻辑
 *
 * 行为:
 *   1. 进入输入框 (focus) -> POST /locks/<rt>/<rid> 尝试获取锁
 *      - 成功: 标记本行"我正在编辑", 启动心跳 + 倒计时
 *      - 失败: 输入框禁用, 提示"已被 XX 锁定, 稍后刷新再试"
 *   2. 编辑中 -> 每 60s 发送心跳续期 (3 分钟有效期)
 *   3. 离开输入框 (blur) -> DELETE /locks/<rt>/<rid> 释放锁
 *   4. 页面卸载 (beforeunload) -> 释放所有自己持有的锁
 *   5. 定时 (每 30s) 拉取当前周期锁列表 -> 同步他人锁定的行
 */
(function () {
  "use strict";

  var cfg = window.EDIT_LOCK || {};
  var YEAR = cfg.year;
  var MONTH = cfg.month;
  var MY_ID = cfg.userId;

  if (!YEAR || !MONTH || !MY_ID) return;  // 非编辑页, 跳过

  // 我持有的锁: { "<rt>:<rid>": true }
  var myLocks = {};
  var heartbeatTimers = {};   // { "<rt>:<rid>": intervalId }
  var countdownTimers = {};   // { "<rt>:<rid>": intervalId }
  var countdownSecs = {};     // { "<rt>:<rid>": remaining }

  function keyOf(rt, rid) { return rt + ":" + rid; }

  function lockStatusEl(rt, rid) {
    return document.getElementById("lock-" + rid);
  }

  function lockInputEl(rt, rid) {
    return document.querySelector(
      'input[data-rt="' + rt + '"][data-rid="' + rid + '"]'
    );
  }

  // ---------- 获取锁 ----------
  function acquireLock(rt, rid, inputEl) {
    fetch("/locks/" + rt + "/" + rid, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ year: YEAR, month: MONTH }),
    }).then(function (r) {
      if (r.ok) {
        return r.json().then(function (d) { onAcquired(rt, rid, d); });
      }
      // 409 冲突
      return r.json().then(function (d) { onConflict(rt, rid, d); });
    }).catch(function () {
      // 网络错误, 不阻塞编辑
    });
  }

  function onAcquired(rt, rid, data) {
    var k = keyOf(rt, rid);
    myLocks[k] = true;
    var secs = data.remaining_seconds || 180;
    var input = lockInputEl(rt, rid);
    if (input) input.disabled = false;
    var el = lockStatusEl(rt, rid);
    if (el) {
      el.setAttribute("data-mine", "1");
      el.textContent = "";
      var span = document.createElement("span");
      span.className = "locked-by-me";
      span.textContent = "编辑中 · ";
      var cd = document.createElement("span");
      cd.className = "countdown";
      cd.setAttribute("data-k", k);
      cd.textContent = secs;
      span.appendChild(cd);
      span.appendChild(document.createTextNode("s"));
      el.appendChild(span);
    }
    startHeartbeat(rt, rid);
    startCountdown(rt, rid, secs);
  }

  function onConflict(rt, rid, data) {
    var input = lockInputEl(rt, rid);
    if (input) input.disabled = true;
    var el = lockStatusEl(rt, rid);
    var who = (data && data.user_label) || "其他用户";
    if (el) {
      el.setAttribute("data-locked", "1");
      el.setAttribute("data-who", who);
      el.textContent = "";
      var span = document.createElement("span");
      span.className = "locked";
      span.textContent = "已锁定 · " + who + " (稍后刷新再试)";
      el.appendChild(span);
    }
    var tr = document.getElementById((rt === "entry" ? "item-" : "acc-") + rid);
    if (tr) tr.classList.add("row-locked");
  }

  // ---------- 心跳续期 ----------
  function startHeartbeat(rt, rid) {
    var k = keyOf(rt, rid);
    stopTimer(heartbeatTimers, k);
    heartbeatTimers[k] = setInterval(function () {
      fetch("/locks/" + rt + "/" + rid + "/heartbeat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ year: YEAR, month: MONTH }),
      }).then(function (r) {
        if (r.ok) {
          return r.json().then(function (d) {
            countdownSecs[k] = d.remaining_seconds || 180;
          });
        } else {
          // 锁被抢占 -> 停止心跳, 提示
          stopTimer(heartbeatTimers, k);
          stopTimer(countdownTimers, k);
          delete myLocks[k];
          return r.json().then(function (d) {
            onConflict(rt, rid, d);
          });
        }
      }).catch(function () {});
    }, 60000);  // 每 60 秒
  }

  // ---------- 倒计时显示 ----------
  function startCountdown(rt, rid, secs) {
    var k = keyOf(rt, rid);
    stopTimer(countdownTimers, k);
    countdownSecs[k] = secs || 180;
    countdownTimers[k] = setInterval(function () {
      countdownSecs[k] -= 1;
      if (countdownSecs[k] <= 0) {
        // 锁过期, 停止计时
        stopTimer(countdownTimers, k);
        stopTimer(heartbeatTimers, k);
        delete myLocks[k];
        var el = lockStatusEl(rt, rid);
        if (el) {
          el.textContent = "";
          var span = document.createElement("span");
          span.className = "muted";
          span.textContent = "锁已过期";
          el.appendChild(span);
        }
        return;
      }
      var cd = document.querySelector('.countdown[data-k="' + k + '"]');
      if (cd) cd.textContent = countdownSecs[k];
    }, 1000);
  }

  function stopTimer(timers, k) {
    if (timers[k]) {
      clearInterval(timers[k]);
      delete timers[k];
    }
  }

  // ---------- 释放锁 ----------
  function releaseLock(rt, rid) {
    var k = keyOf(rt, rid);
    if (!myLocks[k]) return;
    stopTimer(heartbeatTimers, k);
    stopTimer(countdownTimers, k);
    delete myLocks[k];
    fetch("/locks/" + rt + "/" + rid, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ year: YEAR, month: MONTH }),
    }).catch(function () {});
    var el = lockStatusEl(rt, rid);
    if (el) {
      el.removeAttribute("data-mine");
      el.textContent = "";
    }
  }

  // ---------- 同步他人锁 (轮询) ----------
  function syncLocks() {
    var rts = [];
    document.querySelectorAll('input[data-rt]').forEach(function (el) {
      var rt = el.getAttribute("data-rt");
      if (rts.indexOf(rt) === -1) rts.push(rt);
    });
    rts.forEach(function (rt) {
      fetch("/locks/" + rt + "/" + YEAR + "/" + MONTH)
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var locks = d.locks || [];
          locks.forEach(function (lk) {
            var k = keyOf(rt, lk.rid);
            if (lk.user_id !== MY_ID && !myLocks[k]) {
              onConflict(rt, lk.rid, {
                user_label: lk.user_label,
                remaining_seconds: lk.remaining_seconds,
              });
            }
          });
        }).catch(function () {});
    });
  }

  // ---------- 绑定事件 ----------
  function init() {
    var inputs = document.querySelectorAll('input[data-rt][data-rid][type=number]');
    inputs.forEach(function (el) {
      var rt = el.getAttribute("data-rt");
      var rid = el.getAttribute("data-rid");
      // 已被他人锁定的输入框 (模板渲染时 disabled) 不绑定
      if (el.disabled) return;

      el.addEventListener("focus", function () {
        acquireLock(rt, rid, el);
      });
      el.addEventListener("blur", function () {
        releaseLock(rt, rid);
      });
    });

    // 页面卸载 -> 释放所有自己持有的锁 (best-effort, 锁也会 3 分钟后自动过期)
    window.addEventListener("beforeunload", function () {
      Object.keys(myLocks).forEach(function (k) {
        var parts = k.split(":");
        var rt = parts[0], rid = parts[1];
        var body = JSON.stringify({ year: YEAR, month: MONTH });
        try {
          fetch("/locks/" + rt + "/" + rid, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: body,
            keepalive: true,
          });
        } catch (e) { /* 忽略, 锁会自动过期 */ }
      });
    });

    // 每 30 秒同步他人锁
    setInterval(syncLocks, 30000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
