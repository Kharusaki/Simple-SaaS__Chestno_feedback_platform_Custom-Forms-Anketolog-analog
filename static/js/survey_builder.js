(function () {
  "use strict";

  var list = document.getElementById("questions-list");
  var template = document.getElementById("question-template");
  var addButton = document.getElementById("add-question");
  var counter = document.getElementById("questions-counter");
  var form = document.getElementById("survey-form");
  var reviewSelect = document.querySelector("[data-review-select]");

  if (!list || !template || !form) return;

  var maxQuestions = parseInt(form.dataset.maxQuestions, 10) || 30;
  var maxChoices = parseInt(form.dataset.maxChoices, 10) || 20;

  function blocks() {
    return Array.prototype.slice.call(list.querySelectorAll(".question-block"));
  }

  function choiceList(node) {
    return node.querySelector("[data-choice-rows]");
  }

  function renumber() {
    var nodes = blocks();
    nodes.forEach(function (node, position) {
      node.dataset.index = String(position);
      node.querySelectorAll("[name]").forEach(function (field) {
        field.name = field.name.replace(/^q\d+_/, "q" + position + "_");
      });
      var number = node.querySelector(".question-number");
      if (number) number.textContent = String(position + 1);
      /* Ошибка принадлежит блоку, который её вызвал. При добавлении или
         удалении вопроса индексы сдвигаются, и текст про ошибке перестал бы
         относиться к тому вопросу, рядом с которым стоит. */
      var error = node.querySelector(".field-error");
      if (error) {
        error.removeAttribute("data-error-for");
        error.textContent = "";
      }
    });
    if (counter) {
      counter.textContent = nodes.length === 1
        ? "1 вопрос"
        : nodes.length + " вопросов";
    }
    if (addButton) {
      addButton.disabled = nodes.length >= maxQuestions;
    }
  }

  function syncChoices(node) {
    var holder = choiceList(node);
    var target = node.querySelector(".choices-value");
    if (!holder || !target) return;

    var lines = [];
    holder.querySelectorAll(".choice-text").forEach(function (input) {
      var value = input.value.trim();
      if (value) lines.push(value);
    });
    target.value = lines.join("\n");
  }

  function syncCorrect(node) {
    /* Верные варианты едут одним скрытым полем — тем же способом, что и
       сами варианты. Иначе пришлось бы отправлять по полю на каждый
       вариант, и билдер не смог бы отличить «снят флаг» от «не прислали». */
    var target = node.querySelector(".correct-value");
    if (!target) return;
    var lines = [];
    node.querySelectorAll(".choice-correct-check").forEach(function (check) {
      if (check.checked) lines.push(check.dataset.choiceText);
    });
    target.value = lines.join("\n");
  }

  function applyReview(node) {
    /* Поля проверки показываются только когда включён режим. Скрывать их
       сервером нельзя: при ошибке валидации форма перерисовывается, и
       отмеченные «верно» варианты потеряли бы галочки. */
    var active = !!reviewSelect && reviewSelect.value !== "none";
    node.querySelectorAll(".js-review-only, .js-review-hint").forEach(function (field) {
      field.hidden = !active;
    });
  }

  function applyReviewAll() {
    blocks().forEach(applyReview);
  }

  function markerFor(node) {
    return node.querySelector(".question-type").value === "single" ? "radio" : "checkbox";
  }

  function applyMarker(node) {
    var marker = markerFor(node);
    var holder = choiceList(node);
    if (holder) {
      holder.querySelectorAll(".choice-mark").forEach(function (mark) {
        mark.type = marker;
      });
    }
  }

  function makeChoiceRow(marker) {
    var row = document.createElement("div");
    row.className = "choice-row";

    var mark = document.createElement("input");
    mark.type = marker;
    mark.className = "choice-mark";
    mark.disabled = true;
    mark.tabIndex = -1;
    mark.setAttribute("aria-hidden", "true");

    var text = document.createElement("input");
    text.type = "text";
    text.className = "choice-text";
    text.maxLength = 300;
    text.placeholder = "Вариант ответа";

    var correct = document.createElement("label");
    correct.className = "choice-correct js-review-only";
    correct.title = "Правильный вариант";
    var correctCheck = document.createElement("input");
    correctCheck.type = "checkbox";
    correctCheck.className = "choice-correct-check js-review-only";
    var correctText = document.createElement("span");
    correctText.textContent = "верно";
    correct.appendChild(correctCheck);
    correct.appendChild(correctText);

    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "choice-remove js-remove-choice";
    remove.title = "Убрать вариант";
    remove.innerHTML = "&times;";

    row.appendChild(mark);
    row.appendChild(text);
    row.appendChild(correct);
    row.appendChild(remove);
    return row;
  }

  function addChoice(node) {
    var holder = choiceList(node);
    if (!holder) return;
    if (holder.querySelectorAll(".choice-row").length >= maxChoices) return;

    var row = makeChoiceRow(markerFor(node));
    holder.appendChild(row);
    syncChoices(node);
    applyReview(node);
    row.querySelector(".choice-text").focus();
  }

  function removeChoice(node, row) {
    var holder = choiceList(node);
    if (!holder) return;

    row.remove();
    if (!holder.querySelector(".choice-row")) {
      holder.appendChild(makeChoiceRow(markerFor(node)));
    }
    syncChoices(node);
    syncCorrect(node);
  }

  function parseScale(value) {
    var parts = (value || "").split("-");
    if (parts.length !== 2) return null;
    var low = parseInt(parts[0], 10);
    var high = parseInt(parts[1], 10);
    if (isNaN(low) || isNaN(high) || low < 1 || low >= high || high > 10) return null;
    return { low: low, high: high };
  }

  function renderScaleFrame(node) {
    var frame = node.querySelector("[data-scale-frame]");
    if (!frame) return;

    var select = node.querySelector(".question-scale-select");
    var scale = parseScale(select ? select.value : "");
    frame.innerHTML = "";
    if (!scale) return;

    for (var value = scale.low; value <= scale.high; value++) {
      var point = document.createElement("label");
      point.className = "scale-point";

      var input = document.createElement("input");
      input.type = "radio";
      input.disabled = true;
      input.tabIndex = -1;
      input.setAttribute("aria-hidden", "true");

      var bubble = document.createElement("span");
      bubble.className = "scale-bubble";
      bubble.textContent = String(value);

      point.appendChild(input);
      point.appendChild(bubble);
      frame.appendChild(point);
    }
  }

  function applyType(node) {
    var type = node.querySelector(".question-type").value;
    node.querySelectorAll("[data-for]").forEach(function (section) {
      var allowed = section.dataset.for.split(",");
      section.hidden = allowed.indexOf(type) === -1;
    });
    applyMarker(node);
    renderScaleFrame(node);
    syncChoices(node);
    syncCorrect(node);
    applyReview(node);
  }

  function addBlock() {
    var node = template.content.cloneNode(true);
    var wrapper = node.querySelector(".question-block");
    list.appendChild(wrapper);
    applyType(wrapper);
    renumber();
    wrapper.querySelector(".question-text").focus();
  }

  list.addEventListener("click", function (event) {
    var choiceRemove = event.target.closest(".js-remove-choice");
    if (choiceRemove) {
      removeChoice(choiceRemove.closest(".question-block"), choiceRemove.closest(".choice-row"));
      return;
    }

    var addChoiceButton = event.target.closest(".js-add-choice");
    if (addChoiceButton) {
      addChoice(addChoiceButton.closest(".question-block"));
      return;
    }

    var remove = event.target.closest(".js-remove");
    if (!remove) return;
    var node = remove.closest(".question-block");
    var nodes = blocks();
    if (nodes.length === 1) {
      /* Последний вопрос не удаляем, а очищаем: иначе форма осталась бы
       * совсем без вопросов и отправлять было бы нечего. Заодно сбрасываем
       * картинку — иначе она молча уехала бы в следующий отправленный
       * вопрос вместо удалённого. */
      node.querySelector(".question-text").value = "";
      var image = node.querySelector("input[type='file']");
      if (image) image.value = "";
      var keep = node.querySelector("input[type='hidden'][name$='_image_keep']");
      if (keep) keep.value = "";
      var clear = node.querySelector("input[type='checkbox'][name$='_image_clear']");
      if (clear) clear.checked = false;
      var preview = node.querySelector(".image-current");
      if (preview) preview.remove();
      syncChoices(node);
      return;
    }
    node.remove();
    renumber();
  });

  list.addEventListener("change", function (event) {
    var node = event.target.closest(".question-block");
    if (!node) return;

    if (event.target.classList.contains("question-type")) {
      applyType(node);
    } else if (event.target.classList.contains("question-scale-select")) {
      renderScaleFrame(node);
    }
  });

  list.addEventListener("input", function (event) {
    var node = event.target.closest(".question-block");
    if (!node) return;
    if (event.target.classList.contains("choice-text")) {
      var row = event.target.closest(".choice-row");
      var check = row && row.querySelector(".choice-correct-check");
      if (check) {
        /* Галочка «верно» повторяет текст варианта: в скрытое поле уходит
           текст, а не индекс, поэтому переименование варианта не должно
           оставлять в анкете отсылку к старой формулировке. */
        check.dataset.choiceText = event.target.value.trim();
        syncChoices(node);
        syncCorrect(node);
      }
    } else if (event.target.classList.contains("choice-correct-check")) {
      syncCorrect(node);
    }
  });

  if (reviewSelect) {
    reviewSelect.addEventListener("change", applyReviewAll);
  }

  addButton.addEventListener("click", addBlock);

  form.addEventListener("submit", function (event) {
    blocks().forEach(function (node) {
      syncChoices(node);
      syncCorrect(node);
    });

    if (blocks().length === 0) {
      event.preventDefault();
      alert("Добавьте хотя бы один вопрос.");
      return;
    }
    var title = form.querySelector("#title");
    if (title && !title.value.trim()) {
      event.preventDefault();
      title.focus();
      alert("Введите название анкеты.");
    }
  });

  if (blocks().length === 0) addBlock();
  else {
    blocks().forEach(applyType);
    renumber();
  }

  applyReviewAll();
})();
