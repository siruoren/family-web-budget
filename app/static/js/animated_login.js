// ============ Animated Login - Character Interaction ============
// Based on https://github.com/guohaolian/animatedlogin
// Adapted for Flask auth: password field, flash errors, no email field

(function () {
  "use strict";

  var passwordInput = document.getElementById("password");
  var toggleBtn = document.getElementById("toggle-password");
  var eyeIcon = document.getElementById("eye-icon");
  var eyeOffIcon = document.getElementById("eye-off-icon");
  var showPassword = false;
  var isLoginError = false;

  // Check for flash error on page load
  var flashError = document.querySelector(".flash-error");
  if (flashError) {
    isLoginError = true;
    var errEl = document.getElementById("error-msg");
    if (errEl) {
      errEl.textContent = flashError.textContent.trim();
      errEl.style.display = "block";
    }
    if (passwordInput) {
      passwordInput.classList.add("error");
    }
    triggerLoginError();
  }

  // ============ PASSWORD TOGGLE ============
  if (toggleBtn) {
    toggleBtn.addEventListener("click", function () {
      showPassword = !showPassword;
      passwordInput.type = showPassword ? "text" : "password";
      eyeIcon.style.display = showPassword ? "none" : "block";
      eyeOffIcon.style.display = showPassword ? "block" : "none";
      updateCharacters();
      if (showPassword) schedulePeek();
    });
  }

  // ============ MOUSE TRACKING ============
  var mouseX = 0, mouseY = 0;
  var isTyping = false;
  var isLookingAtEachOther = false;
  var isPurpleBlinking = false;
  var isBlackBlinking = false;
  var isPurplePeeking = false;
  var typingTimer = null;
  var isPasswordFocused = false;

  document.addEventListener("mousemove", function (e) {
    mouseX = e.clientX;
    mouseY = e.clientY;
    if (!isTyping && !isLoginError) updateCharacters();
  });

  // Typing detection (password field acts as email field for character reactions)
  if (passwordInput) {
    // mousedown 主动触发"角色互相看"过渡动画。
    // 背景: autofocus 或已聚焦时点击 password 不会再次派发 focus 事件,
    // 导致"直接点密码框"无过渡动画,而"先点用户下拉再点密码框"(select 抢焦点→password blur→重新 focus)却有动画,
    // 两者不一致。绑定 mousedown 后,无论是否已聚焦,主动点击都会重置 setTyping(true),
    // 与"切换焦点后点击"路径行为一致。
    passwordInput.addEventListener("mousedown", function () {
      setTyping(true);
    });
    passwordInput.addEventListener("focus", function () {
      isPasswordFocused = true;
      setTyping(true);
      updateCharacters();
    });
    passwordInput.addEventListener("blur", function () {
      isPasswordFocused = false;
      setTyping(false);
      updateCharacters();
    });
    passwordInput.addEventListener("input", function () {
      updateCharacters();
    });
  }

  function setTyping(typing) {
    isTyping = typing;
    if (typing) {
      isLookingAtEachOther = true;
      clearTimeout(typingTimer);
      typingTimer = setTimeout(function () {
        isLookingAtEachOther = false;
        updateCharacters();
      }, 800);
    } else {
      isLookingAtEachOther = false;
    }
    updateCharacters();
  }

  // Blinking
  function scheduleBlinkPurple() {
    setTimeout(function () {
      isPurpleBlinking = true;
      updateCharacters();
      setTimeout(function () {
        isPurpleBlinking = false;
        updateCharacters();
        scheduleBlinkPurple();
      }, 150);
    }, Math.random() * 4000 + 3000);
  }

  function scheduleBlinkBlack() {
    setTimeout(function () {
      isBlackBlinking = true;
      updateCharacters();
      setTimeout(function () {
        isBlackBlinking = false;
        updateCharacters();
        scheduleBlinkBlack();
      }, 150);
    }, Math.random() * 4000 + 3000);
  }

  scheduleBlinkPurple();
  scheduleBlinkBlack();

  // Purple peeking when password is visible
  function schedulePeek() {
    if (passwordInput && passwordInput.value.length > 0 && showPassword) {
      setTimeout(function () {
        if (passwordInput.value.length > 0 && showPassword) {
          isPurplePeeking = true;
          updateCharacters();
          setTimeout(function () {
            isPurplePeeking = false;
            updateCharacters();
            schedulePeek();
          }, 800);
        }
      }, Math.random() * 3000 + 2000);
    }
  }

  // ============ CHARACTER POSITION CALC ============
  function calcPosition(el) {
    var rect = el.getBoundingClientRect();
    var cx = rect.left + rect.width / 2;
    var cy = rect.top + rect.height / 3;
    var dx = mouseX - cx;
    var dy = mouseY - cy;
    var faceX = Math.max(-15, Math.min(15, dx / 20));
    var faceY = Math.max(-10, Math.min(10, dy / 30));
    var bodySkew = Math.max(-6, Math.min(6, -dx / 120));
    return { faceX: faceX, faceY: faceY, bodySkew: bodySkew };
  }

  function calcPupilOffset(el, maxDist) {
    var rect = el.getBoundingClientRect();
    var cx = rect.left + rect.width / 2;
    var cy = rect.top + rect.height / 2;
    var dx = mouseX - cx;
    var dy = mouseY - cy;
    var dist = Math.min(Math.sqrt(dx * dx + dy * dy), maxDist);
    var angle = Math.atan2(dy, dx);
    return { x: Math.cos(angle) * dist, y: Math.sin(angle) * dist };
  }

  function updateCharacters() {
    var purple = document.getElementById("char-purple");
    var black = document.getElementById("char-black");
    var orange = document.getElementById("char-orange");
    var yellow = document.getElementById("char-yellow");
    if (!purple || !black || !orange || !yellow) return;

    var purplePos = calcPosition(purple);
    var blackPos = calcPosition(black);
    var orangePos = calcPosition(orange);
    var yellowPos = calcPosition(yellow);

    var pwdLen = passwordInput ? passwordInput.value.length : 0;
    var isShowingPwd = pwdLen > 0 && showPassword;
    var isLookingAway = isPasswordFocused && !showPassword;

    // ---- Purple body ----
    if (isShowingPwd) {
      purple.style.transform = "skewX(0deg)";
      purple.style.height = "370px";
    } else if (isLookingAway) {
      purple.style.transform = "skewX(-14deg) translateX(-20px)";
      purple.style.height = "410px";
    } else if (isTyping) {
      purple.style.transform = "skewX(" + ((purplePos.bodySkew || 0) - 12) + "deg) translateX(40px)";
      purple.style.height = "410px";
    } else {
      purple.style.transform = "skewX(" + purplePos.bodySkew + "deg)";
      purple.style.height = "370px";
    }

    // Purple eyes
    var purpleEyes = document.getElementById("purple-eyes");
    var purpleEyeL = document.getElementById("purple-eye-l");
    var purpleEyeR = document.getElementById("purple-eye-r");
    var purplePupilL = document.getElementById("purple-pupil-l");
    var purplePupilR = document.getElementById("purple-pupil-r");

    if (purpleEyeL) purpleEyeL.style.height = isPurpleBlinking ? "2px" : "18px";
    if (purpleEyeR) purpleEyeR.style.height = isPurpleBlinking ? "2px" : "18px";

    if (isLoginError) {
      purpleEyes.style.left = "30px"; purpleEyes.style.top = "55px";
      purplePupilL.style.transform = "translate(-3px, 4px)";
      purplePupilR.style.transform = "translate(-3px, 4px)";
    } else if (isLookingAway) {
      purpleEyes.style.left = "20px"; purpleEyes.style.top = "25px";
      purplePupilL.style.transform = "translate(-5px, -5px)";
      purplePupilR.style.transform = "translate(-5px, -5px)";
    } else if (isShowingPwd) {
      purpleEyes.style.left = "20px"; purpleEyes.style.top = "35px";
      var px = isPurplePeeking ? 4 : -4;
      var py = isPurplePeeking ? 5 : -4;
      purplePupilL.style.transform = "translate(" + px + "px, " + py + "px)";
      purplePupilR.style.transform = "translate(" + px + "px, " + py + "px)";
    } else if (isLookingAtEachOther) {
      purpleEyes.style.left = "55px"; purpleEyes.style.top = "65px";
      purplePupilL.style.transform = "translate(3px, 4px)";
      purplePupilR.style.transform = "translate(3px, 4px)";
    } else {
      purpleEyes.style.left = (45 + purplePos.faceX) + "px";
      purpleEyes.style.top = (40 + purplePos.faceY) + "px";
      var po = calcPupilOffset(purpleEyeL, 5);
      purplePupilL.style.transform = "translate(" + po.x + "px, " + po.y + "px)";
      purplePupilR.style.transform = "translate(" + po.x + "px, " + po.y + "px)";
    }

    // ---- Black body ----
    if (isShowingPwd) {
      black.style.transform = "skewX(0deg)";
    } else if (isLookingAway) {
      black.style.transform = "skewX(12deg) translateX(-10px)";
    } else if (isLookingAtEachOther) {
      black.style.transform = "skewX(" + ((blackPos.bodySkew || 0) * 1.5 + 10) + "deg) translateX(20px)";
    } else if (isTyping) {
      black.style.transform = "skewX(" + ((blackPos.bodySkew || 0) * 1.5) + "deg)";
    } else {
      black.style.transform = "skewX(" + blackPos.bodySkew + "deg)";
    }

    // Black eyes
    var blackEyes = document.getElementById("black-eyes");
    var blackEyeL = document.getElementById("black-eye-l");
    var blackEyeR = document.getElementById("black-eye-r");
    var blackPupilL = document.getElementById("black-pupil-l");
    var blackPupilR = document.getElementById("black-pupil-r");

    if (blackEyeL) blackEyeL.style.height = isBlackBlinking ? "2px" : "16px";
    if (blackEyeR) blackEyeR.style.height = isBlackBlinking ? "2px" : "16px";

    if (isLoginError) {
      blackEyes.style.left = "15px"; blackEyes.style.top = "40px";
      blackPupilL.style.transform = "translate(-3px, 4px)";
      blackPupilR.style.transform = "translate(-3px, 4px)";
    } else if (isLookingAway) {
      blackEyes.style.left = "10px"; blackEyes.style.top = "20px";
      blackPupilL.style.transform = "translate(-4px, -5px)";
      blackPupilR.style.transform = "translate(-4px, -5px)";
    } else if (isShowingPwd) {
      blackEyes.style.left = "10px"; blackEyes.style.top = "28px";
      blackPupilL.style.transform = "translate(-4px, -4px)";
      blackPupilR.style.transform = "translate(-4px, -4px)";
    } else if (isLookingAtEachOther) {
      blackEyes.style.left = "32px"; blackEyes.style.top = "12px";
      blackPupilL.style.transform = "translate(0px, -4px)";
      blackPupilR.style.transform = "translate(0px, -4px)";
    } else {
      blackEyes.style.left = (26 + blackPos.faceX) + "px";
      blackEyes.style.top = (32 + blackPos.faceY) + "px";
      var bo = calcPupilOffset(blackEyeL, 4);
      blackPupilL.style.transform = "translate(" + bo.x + "px, " + bo.y + "px)";
      blackPupilR.style.transform = "translate(" + bo.x + "px, " + bo.y + "px)";
    }

    // ---- Orange body ----
    var orangeMouth = document.getElementById("orange-mouth");
    if (isLoginError && orangeMouth) {
      orangeMouth.style.left = (80 + orangePos.faceX) + "px";
      orangeMouth.style.top = "130px";
    }
    if (isShowingPwd) {
      orange.style.transform = "skewX(0deg)";
    } else {
      orange.style.transform = "skewX(" + orangePos.bodySkew + "deg)";
    }

    var orangeEyes = document.getElementById("orange-eyes");
    var orangePupilL = document.getElementById("orange-pupil-l");
    var orangePupilR = document.getElementById("orange-pupil-r");

    if (isLoginError) {
      orangeEyes.style.left = "60px"; orangeEyes.style.top = "95px";
      orangePupilL.style.transform = "translate(-3px, 4px)";
      orangePupilR.style.transform = "translate(-3px, 4px)";
    } else if (isLookingAway) {
      orangeEyes.style.left = "50px"; orangeEyes.style.top = "75px";
      orangePupilL.style.transform = "translate(-5px, -5px)";
      orangePupilR.style.transform = "translate(-5px, -5px)";
    } else if (isShowingPwd) {
      orangeEyes.style.left = "50px"; orangeEyes.style.top = "85px";
      orangePupilL.style.transform = "translate(-5px, -4px)";
      orangePupilR.style.transform = "translate(-5px, -4px)";
    } else {
      orangeEyes.style.left = (82 + orangePos.faceX) + "px";
      orangeEyes.style.top = (90 + orangePos.faceY) + "px";
      var oo = calcPupilOffset(orangePupilL, 5);
      orangePupilL.style.transform = "translate(" + oo.x + "px, " + oo.y + "px)";
      orangePupilR.style.transform = "translate(" + oo.x + "px, " + oo.y + "px)";
    }

    // ---- Yellow body ----
    if (isShowingPwd) {
      yellow.style.transform = "skewX(0deg)";
    } else {
      yellow.style.transform = "skewX(" + yellowPos.bodySkew + "deg)";
    }

    var yellowEyes = document.getElementById("yellow-eyes");
    var yellowPupilL = document.getElementById("yellow-pupil-l");
    var yellowPupilR = document.getElementById("yellow-pupil-r");
    var yellowMouth = document.getElementById("yellow-mouth");

    if (isLoginError) {
      yellowEyes.style.left = "35px"; yellowEyes.style.top = "45px";
      yellowPupilL.style.transform = "translate(-3px, 4px)";
      yellowPupilR.style.transform = "translate(-3px, 4px)";
      yellowMouth.style.left = "30px"; yellowMouth.style.top = "92px";
      yellowMouth.style.transform = "rotate(-8deg)";
    } else if (isLookingAway) {
      yellowEyes.style.left = "20px"; yellowEyes.style.top = "30px";
      yellowPupilL.style.transform = "translate(-5px, -5px)";
      yellowPupilR.style.transform = "translate(-5px, -5px)";
      yellowMouth.style.left = "15px"; yellowMouth.style.top = "78px";
      yellowMouth.style.transform = "rotate(0deg)";
    } else if (isShowingPwd) {
      yellowEyes.style.left = "20px"; yellowEyes.style.top = "35px";
      yellowPupilL.style.transform = "translate(-5px, -4px)";
      yellowPupilR.style.transform = "translate(-5px, -4px)";
      yellowMouth.style.left = "10px"; yellowMouth.style.top = "88px";
      yellowMouth.style.transform = "rotate(0deg)";
    } else {
      yellowEyes.style.left = (52 + yellowPos.faceX) + "px";
      yellowEyes.style.top = (40 + yellowPos.faceY) + "px";
      var yo = calcPupilOffset(yellowPupilL, 5);
      yellowPupilL.style.transform = "translate(" + yo.x + "px, " + yo.y + "px)";
      yellowPupilR.style.transform = "translate(" + yo.x + "px, " + yo.y + "px)";
      yellowMouth.style.left = (40 + yellowPos.faceX) + "px";
      yellowMouth.style.top = (88 + yellowPos.faceY) + "px";
      yellowMouth.style.transform = "rotate(0deg)";
    }
  }

  // ============ LOGIN ERROR ANIMATION ============
  var errorRecoverTimer = null;
  var shakeIds = [
    "purple-eyes", "black-eyes", "orange-eyes",
    "yellow-eyes", "yellow-mouth", "orange-mouth",
  ];

  function triggerLoginError() {
    if (errorRecoverTimer) {
      clearTimeout(errorRecoverTimer);
      errorRecoverTimer = null;
    }

    var shakeEls = shakeIds.map(function (id) { return document.getElementById(id); }).filter(Boolean);
    shakeEls.forEach(function (el) { el.classList.remove("shake-head"); });
    void document.body.offsetHeight;

    isLoginError = true;
    isPasswordFocused = false;
    updateCharacters();

    var om = document.getElementById("orange-mouth");
    if (om) om.classList.add("visible");

    setTimeout(function () {
      shakeEls.forEach(function (el) { el.classList.add("shake-head"); });
    }, 350);

    errorRecoverTimer = setTimeout(function () {
      isLoginError = false;
      errorRecoverTimer = null;
      if (om) om.classList.remove("visible");
      shakeEls.forEach(function (el) { el.classList.remove("shake-head"); });
      updateCharacters();
    }, 2500);
  }

  // Initial render
  updateCharacters();
})();
