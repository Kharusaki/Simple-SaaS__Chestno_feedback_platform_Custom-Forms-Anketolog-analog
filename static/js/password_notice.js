/* Окно «пароль стандартный». Показывается сразу при загрузке страницы, но
 * скрытый в разметке: без JS пользователь просто ничего не увидит, зато
 * страница не останется с мёртвым серым экраном.
 *
 * Кнопка «Закрыть» ничего не меняет на сервере — предупреждение снова
 * появится при следующей загрузке, если пароль так и не поменяли.
 */
(function () {
  "use strict";

  var modal = document.getElementById("password-modal");
  if (!modal) {
    return;
  }

  function show() {
    modal.hidden = false;
    var focusable = modal.querySelector('input[type="checkbox"]');
    if (focusable) {
      focusable.focus();
    }
  }

  function hide() {
    modal.hidden = true;
  }

  var close = modal.querySelector("[data-close-password-modal]");
  if (close) {
    close.addEventListener("click", hide);
  }

  /* Клик по подложке закрывает окно, но клик внутри — нет: иначе окно
   * закрывалось бы в тот момент, когда человек тянется к галочке. */
  modal.addEventListener("click", function (event) {
    if (event.target === modal) {
      hide();
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      hide();
    }
  });

  show();
})();
