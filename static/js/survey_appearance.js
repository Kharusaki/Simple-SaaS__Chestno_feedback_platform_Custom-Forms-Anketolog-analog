(function () {
  "use strict";

  /* Меню оформления живёт в одном шаблоне и подключается и при создании,
     и в редакторе, поэтому скрипт ищет его по data-атрибутам, а не по
     классу конкретной страницы. Иначе второе подключение молча перестало
     бы работать. */

  var root = document.querySelector("[data-appearance]");
  if (!root) return;

  var preview = root.querySelector("[data-preview]");
  var themeSelect = root.querySelector("[data-theme-select]");
  var fontSelect = root.querySelector("[data-font-select]");
  var catalogNode = root.querySelector("[data-appearance-catalog]");
  if (!preview || !themeSelect || !fontSelect || !catalogNode) return;

  var catalog;
  try {
    catalog = JSON.parse(catalogNode.textContent);
  } catch (error) {
    return;
  }

  var statusNode = root.querySelector("[data-preview-status]");

  /* Отклик на «Применить». Превью и так перерисовывается на каждый ввод, но
     сам по себе факт перерисовки человек не видит: цвет подложки меняется
     на глазах и легко пропустить, а на тёмной анкете он вдобавок тянет за
     собой пересчёт текста. Поэтому кнопка не только применяет выбор, но и
     подтверждает его словами и вспышкой рамки — иначе «применил, и ничего
     не изменилось» неотличимо от «кнопка не сработала». */
  function announce(message) {
    if (statusNode) statusNode.textContent = message;
    preview.classList.add("is-applied");
    if (announce.timer) window.clearTimeout(announce.timer);
    announce.timer = window.setTimeout(function () {
      preview.classList.remove("is-applied");
    }, 700);
  }

  function optionLabel(select) {
    var option = select.options[select.selectedIndex];
    return option ? option.textContent.trim() : "";
  }

  /* Превью рисуется теми же переменными, что и публичная страница: сервер
     отдаёт готовые `--primary` и `--bg` для каждой темы, браузер только
     склеивает их с выбранными шрифтом. Свою палитру в JS не дублируем —
     иначе она разошлась бы с themes.py при первой же правке темы. */
  function apply() {
    var chosen = {};
    var theme = catalog["theme:" + themeSelect.value];
    var font = catalog["font:" + fontSelect.value];

    if (theme) {
      Object.keys(theme).forEach(function (name) {
        chosen[name] = theme[name];
      });
    }
    if (font) {
      Object.keys(font).forEach(function (name) {
        chosen[name] = font[name];
      });
    }

    applyColors(chosen);

    var style = preview.style;
    Object.keys(chosen).forEach(function (name) {
      style.setProperty(name, chosen[name]);
    });

    var title = root.querySelector("[data-preview-title]");
    var description = root.querySelector("[data-preview-description]");
    var source = document.getElementById("title");
    var sourceDescription = document.getElementById("description");

    if (title && source) title.textContent = source.value.trim() || "Название анкеты";
    if (description && sourceDescription) {
      description.textContent = sourceDescription.value.trim() || "Описание анкеты";
    }
  }

  /* --- Свои цвета ------------------------------------------------------- */

  /* Светлоту считаем по той же формуле WCAG, что и themes.py, иначе
     превью врало бы: человек подставил бы тёмную подложку, увидел бы
     чёрный текст на ней и решил, что меню сломано. */
  function parseHex(value) {
    var text = String(value || "").trim();
    if (/^#[0-9a-fA-F]{3}$/.test(text)) {
      text = "#" + text.slice(1).split("").map(function (ch) { return ch + ch; }).join("");
    }
    return /^#[0-9a-fA-F]{6}$/.test(text) ? text.toLowerCase() : null;
  }

  function channelLuminance(value) {
    var srgb = value / 255;
    return srgb <= 0.03928 ? srgb / 12.92 : Math.pow((srgb + 0.055) / 1.055, 2.4);
  }

  function luminance(hex) {
    var color = parseHex(hex);
    if (!color) return 1;
    var r = parseInt(color.slice(1, 3), 16);
    var g = parseInt(color.slice(3, 5), 16);
    var b = parseInt(color.slice(5, 7), 16);
    return (
      0.2126 * channelLuminance(r) +
      0.7152 * channelLuminance(g) +
      0.0722 * channelLuminance(b)
    );
  }

  function readableInk(bg) {
    return luminance(bg) > 0.179 ? "#141414" : "#ffffff";
  }

  function readableMuted(bg) {
    return luminance(bg) > 0.179 ? "#4f4f4c" : "#b8b8b2";
  }

  function blend(base, toward, ratio) {
    var out = "#";
    [1, 3, 5].forEach(function (start) {
      var a = parseInt(base.slice(start, start + 2), 16);
      var b = parseInt(toward.slice(start, start + 2), 16);
      var value = Math.round(a + (b - a) * ratio);
      out += (value < 16 ? "0" : "") + value.toString(16);
    });
    return out;
  }

  function colorInput(name) {
    return root.querySelector("[data-color-text='" + name + "']");
  }

  /* Видимое поле показывает действующий цвет, скрытое — настоящее значение
     для сохранения. Пока человек ничего не задал, скрытое пустое, и цвет
     темы не превращается в собственный. */
  function colorOverride(name) {
    return root.querySelector("[data-color-value='" + name + "']");
  }

  function themeDefault(name) {
    var picker = root.querySelector("[data-color-picker='" + name + "']");
    return picker ? picker.dataset.default || "" : "";
  }

  function colorLabel(name) {
    var field = root.querySelector("[data-color-field='" + name + "']");
    var caption = field && field.querySelector(".color-caption");
    return caption ? caption.textContent.trim() : "цвета";
  }

  /* Пипетка, видимое поле и скрытое значение — одно и то же состояние в
     трёх местах. Раньше «Применить» менял только превью, и образец оставался
     прежним: человек вписал цвет, нажал кнопку и видел квадрат старого
     цвета, откуда и вывод «не фиксируется». */
  function syncColorInputs(name, parsed) {
    var picker = root.querySelector("[data-color-picker='" + name + "']");
    var field = colorInput(name);
    var override = colorOverride(name);
    var fallback = themeDefault(name);

    if (override) override.value = parsed || "";
    if (field) field.value = parsed || fallback;
    if (picker) picker.value = parsed || fallback || picker.value;

    var own = Boolean(parsed);
    if (picker) picker.classList.toggle("is-own", own);

    var resetOne = root.querySelector("[data-color-reset='" + name + "']");
    if (resetOne) resetOne.hidden = !own;
  }

  function setError(name, message) {
    var node = root.querySelector("[data-color-error='" + name + "']");
    var field = colorInput(name);
    if (node) node.textContent = message || "";
    if (field) {
      field.classList.toggle("is-invalid", Boolean(message));
      if (message) field.setAttribute("aria-invalid", "true");
      else field.removeAttribute("aria-invalid");
    }
  }

  /* Разбор цвета идёт от скрытого поля, а не от видимого: видимое поле
     показывает цвет темы, иначе анкета без своих цветов сохранила бы тему
     как собственный цвет и перестала бы за ней следовать. */
  function overrideHex(name) {
    var override = colorOverride(name);
    return override ? parseHex(override.value) : null;
  }

  function applyColors(chosen) {
    var background = overrideHex("bg_color");
    var ink = overrideHex("ink_color");
    var accent = overrideHex("accent_color");

    if (background) {
      var text = ink || readableInk(background);
      chosen["--bg"] = background;
      chosen["--text"] = text;
      chosen["--muted"] = readableMuted(background);
      /* Карточки и рамки тянутся из подложки, иначе блоки вопросов
         остались бы вырезанными из чужой темы. */
      chosen["--surface"] = blend(background, text, 0.08);
      chosen["--border"] = blend(background, text, 0.22);
      chosen["--primary-soft"] = blend(background, text, 0.14);
    }
    if (accent) {
      chosen["--primary"] = accent;
      chosen["--primary-dark"] = accent;
      chosen["--signal"] = accent;
    }
    return chosen;
  }

  /* Пипетка и текстовое поле — две стороны одного значения. Меняем любую,
     вторая подтягивается сама; без обратной связи человек вписал бы цвет
     и не увидел бы результата. */
  ["bg_color", "accent_color", "ink_color"].forEach(function (name) {
    var text = colorInput(name);
    var picker = root.querySelector("[data-color-picker='" + name + "']");
    if (!text) return;

    if (picker) {
      picker.addEventListener("input", function () {
        syncColorInputs(name, picker.value);
        setError(name, "");
        apply();
        /* Пипетка — выбор одним движением, в отличие от набора кода, и
           заслуживает подтверждения: иначе строка статуса остаётся с прошлым
           отказом, хотя цвет уже сменился. */
        announce('Цвет «' + colorLabel(name) + '» применён: ' + picker.value.toLowerCase() + '.');
      });
    }

    text.addEventListener("input", function () {
      var raw = text.value.trim();
      var parsed = parseHex(raw);

      if (!raw) {
        setError(name, "");
        syncColorInputs(name, null);
      } else if (!parsed) {
        /* Не трогаем образец и скрытое значение: человек ещё дописывает,
           иначе недописанное значение уедет в превью и в базу. */
        setError(name, "Нужен цвет в виде #fff или #ffffff.");
      } else {
        setError(name, "");
        /* Приводим к полной форме прямо в поле: иначе при сохранении
           значение уедет в базу в виде #fff, а человек так и не увидит
           итоговую запись. */
        syncColorInputs(name, parsed);
      }
      apply();
    });

    text.addEventListener("blur", function () {
      var parsed = parseHex(text.value.trim());
      if (!text.value.trim() || parsed) {
        setError(name, "");
        syncColorInputs(name, parsed || null);
        apply();
      }
    });

    var resetOne = root.querySelector("[data-color-reset='" + name + "']");
    if (resetOne) {
      resetOne.addEventListener("click", function () {
        setError(name, "");
        syncColorInputs(name, null);
        resetOne.hidden = true;
        apply();
        announce('Свой цвет «' + colorLabel(name) + '» снят, у анкеты цвет темы.');
      });
    }
  });

  var reset = root.querySelector("[data-colors-reset]");
  if (reset) {
    reset.addEventListener("click", function () {
      ["bg_color", "accent_color", "ink_color"].forEach(function (name) {
        var resetOne = root.querySelector("[data-color-reset='" + name + "']");
        syncColorInputs(name, null);
        setError(name, "");
        if (resetOne) resetOne.hidden = true;
      });
      apply();
    });
  }

  /* Цвет темы нужен не только пипетке, но и кнопке «Применить»: человек,
     не меняя ничего, жмёт её и ждёт, что поле зафиксирует его выбор. Если
     в поле лежит ровно цвет темы, это не «свой цвет», а отказ от него.
     Берём значение из каталога, а не из пипетки: у сохранённой анкеты в
     пипетке уже лежит СВОЙ цвет, и сравнение выдало бы его за цвет темы. */
  function syncThemeDefaults() {
    var theme = catalog["theme:" + themeSelect.value];
    if (!theme) return;
    ["bg_color", "accent_color", "ink_color"].forEach(function (name) {
      var picker = root.querySelector("[data-color-picker='" + name + "']");
      var key = { bg_color: "--bg", accent_color: "--primary", ink_color: "--text" }[name];
      if (picker && theme[key]) {
        picker.dataset.default = theme[key];
        /* Заданный руками цвет переживает смену темы, а незаданный -
         показывает новую тему и в образце, и в поле. */
        if (!colorOverride(name).value) {
          syncColorInputs(name, null);
        }
      }
    });
  }

  themeSelect.addEventListener("change", function () {
    syncThemeDefaults();
    apply();
  });

  /* --- Кнопки «Применить» --------------------------------------------- */

  var applyTheme = root.querySelector('[data-apply="theme"]');
  if (applyTheme) {
    applyTheme.addEventListener("click", function () {
      syncThemeDefaults();
      apply();
      announce('Тема «' + optionLabel(themeSelect) + '» применена к превью.');
    });
  }

  var applyFont = root.querySelector('[data-apply="font"]');
  if (applyFont) {
    applyFont.addEventListener("click", function () {
      loadGoogleFont();
      apply();
      announce('Шрифт «' + optionLabel(fontSelect) + '» применён к превью.');
    });
  }

  /* Цвет применяется по одному: кнопка не должна молча брать и подложку, и
     акцент, ибо непонятно, что именно изменилось. Неразборчивый hex
     отсекаем здесь же, иначе человек нажал «Применить» и получил старый
     цвет с ошибкой в соседнем поле. */
  root.querySelectorAll("[data-apply-color]").forEach(function (button) {
    button.addEventListener("click", function () {
      var name = button.getAttribute("data-apply-color");
      var label = colorLabel(name);
      var field = colorInput(name);
      var raw = field ? field.value.trim() : "";

      if (raw && !parseHex(raw)) {
        setError(name, "Нужен цвет в виде #fff или #ffffff.");
        announce('Цвет «' + label + '» не применён: проверьте значение.');
        return;
      }

      /* Поле может содержать ровно то, что показывает образец (цвет темы),
         и тогда это не «свой цвет», а явный отказ от него. */
      var fallback = themeDefault(name);
      var parsed = parseHex(raw);
      var isOwn = Boolean(parsed) && parsed !== fallback;
      if (!isOwn) {
        raw = "";
        parsed = null;
      }

      syncColorInputs(name, parsed);
      var resetOne = root.querySelector("[data-color-reset='" + name + "']");
      if (resetOne) resetOne.hidden = !isOwn;

      apply();
      announce(
        isOwn
          ? 'Цвет «' + label + '» применён: ' + raw.toLowerCase() + '.'
          : 'Свой цвет «' + label + '» снят, у анкеты цвет темы.'
      );
    });
  });

  fontSelect.addEventListener("change", loadGoogleFont);

  /* Google Fonts подключается на лету: список из 11 семейств в одной ссылке
     весил бы десятки килобайт на каждую страницу, а нужен ровно один.
     injectOnce не даёт добавить один и тот же шрифт повторно. */
  var injected = Object.create(null);

  function loadGoogleFont() {
    var option = fontSelect.options[fontSelect.selectedIndex];
    var family = option ? option.dataset.google : "";
    if (!family || injected[family]) return;
    injected[family] = true;

    var url =
      "https://fonts.googleapis.com/css2?family=" +
      encodeURIComponent(family).replace(/%20/g, "+") +
      ":wght@400;500;600;700&display=swap";
    var link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = url;
    document.head.appendChild(link);
  }

  themeSelect.addEventListener("change", apply);
  loadGoogleFont();
  syncThemeDefaults();
  apply();
})();
