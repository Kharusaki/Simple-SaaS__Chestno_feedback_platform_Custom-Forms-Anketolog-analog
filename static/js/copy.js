(function () {
  "use strict";

  function flash(button, message) {
    var original = button.dataset.label || button.textContent;
    button.dataset.label = original;
    button.textContent = message;
    button.classList.add("is-copied");
    window.setTimeout(function () {
      button.textContent = original;
      button.classList.remove("is-copied");
    }, 1800);
  }

  async function copy(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
    var helper = document.createElement("textarea");
    helper.value = text;
    helper.setAttribute("readonly", "");
    helper.style.position = "fixed";
    helper.style.opacity = "0";
    document.body.appendChild(helper);
    helper.select();
    var ok = document.execCommand("copy");
    document.body.removeChild(helper);
    return ok;
  }

  document.addEventListener("click", async function (event) {
    var button = event.target.closest("[data-copy]");
    if (!button) return;
    event.preventDefault();
    try {
      var ok = await copy(button.dataset.copy);
      flash(button, ok ? "Скопировано" : "Не вышло");
    } catch (error) {
      flash(button, "Скопируйте вручную");
    }
  });
})();
