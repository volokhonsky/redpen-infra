/**
 * Категория аннотации — один из шести приёмов пропаганды либо «Прочее».
 *
 * Близнец `scripts/remark_categories.py`, который является источником правды.
 * Дублируется, а не импортируется, потому что просмотрщик обязан работать без
 * сети, без сборки и с флешки (docs/README.md, «Ключевое ограничение»).
 * Расхождение двух копий ловит `tests/test_remark_categories.py` —
 * правя одну, правь обе.
 *
 * Дизайн и обоснование палитры: docs/annotation-classification-2026-08.md.
 *
 * Здесь нет ни одного обращения к сети и не должно появиться.
 */
(function (global) {
  'use strict';

  var CAT_PREFIX = 'cat:';
  var OTHER = 'other';

  // Порядок = приоритет. Раньше в списке — сильнее. Порядок нужен потому, что
  // теги в БД лежат отсортированными по алфавиту (ORDER BY tag в db.py), то есть
  // «первый тег автора» до просмотрщика не доезжает.
  var PRECEDENCE = ['today', 'apparatus', 'sides', 'language', 'evidence', 'omission'];

  var TITLES = {
    omission: 'Умолчание',
    language: 'Обтекаемый язык',
    sides: 'Двойной стандарт',
    evidence: 'Нечем подтвердить',
    apparatus: 'Подсказанный ответ',
    today: 'Мостик в сегодня',
    other: 'Прочее'
  };

  var COLORS = {
    omission: '#1B4F9C',
    language: '#00695C',
    sides: '#C08A00',
    evidence: '#6A1B9A',
    apparatus: '#C2185B',
    today: '#8E1B14',
    other: '#546E7A'
  };

  // Слабые теги: учитываются, только если сильных не нашлось. `framing` стоит на
  // 337 аннотациях и означает четыре разные вещи — разбор в доке.
  var WEAK_TAGS = { framing: 'evidence' };

  var TAG_CATEGORIES = {
    // 1. Умолчание
    'omission': 'omission',
    'tc-censorship-unnamed': 'omission',
    'tc-censorship-invisible': 'omission',
    'tc-record-not-result': 'omission',
    'tc-victim-hero': 'omission',
    'tc-thaw-no-ending': 'omission',
    'tc-hidden-disasters': 'omission',
    'tc-antisemitism-hidden': 'omission',
    'tc-famine-1946': 'omission',
    'tc-key-concept-unexplained': 'omission',
    'tc-flattering-portrait': 'omission',
    'tc-sanitized-chekist': 'omission',
    'tc-invisible-gulag': 'omission',
    // 2. Обтекаемый язык
    'euphemism': 'language',
    'passive-voice': 'language',
    'tc-passive-voice': 'language',
    'tc-annexation-euphemism': 'language',
    'tc-despite-not-because': 'language',
    'tc-permission-as-abundance': 'language',
    'tc-import-as-cooperation': 'language',
    'tc-joke-instead-of-analysis': 'language',
    'tc-strangeness-as-explanation': 'language',
    // 3. Двойной стандарт
    'false-cause': 'sides',
    'double-standard': 'sides',
    'false-symmetry': 'sides',
    'tc-usa-origin': 'sides',
    'tc-whatabout': 'sides',
    'tc-defensive-bloc': 'sides',
    'tc-west-economic-scapegoat': 'sides',
    'tc-west-broken-promises': 'sides',
    'tc-destalinization-blamed': 'sides',
    'tc-blame-the-consumer': 'sides',
    'tc-red-army-invisible': 'sides',
    'tc-selective-aggression-label': 'sides',
    'tc-reunification-annexation': 'sides',
    'tc-invited-intervention': 'sides',
    'tc-reluctant-aggressor': 'sides',
    'tc-democratization-as-collapse': 'sides',
    'tc-crisis-without-mechanism': 'sides',
    'tc-reform-as-powergrab': 'sides',
    'tc-reform-labeled-radical': 'sides',
    'tc-foreign-praise-inverted': 'sides',
    // 4. Нечем подтвердить
    'source-selection': 'evidence',
    'dubious-number': 'evidence',
    'contested-as-settled': 'evidence',
    'overclaim': 'evidence',
    'tc-agitprop-as-source': 'evidence',
    'tc-official-stats': 'evidence',
    'tc-only-friendly-witnesses': 'evidence',
    'tc-one-sided-ledger': 'evidence',
    'tc-foreign-praise': 'evidence',
    'tc-anonymous-superlative': 'evidence',
    'tc-author-vouches': 'evidence',
    'tc-leader-science': 'evidence',
    'tc-constitution-as-evidence': 'evidence',
    'tc-showcase-photo': 'evidence',
    'tc-uneven-portrait': 'evidence',
    'tc-moscow-as-country': 'evidence',
    'tc-benefit-through-employer': 'evidence',
    'tc-single-source-conflict': 'evidence',
    // 5. Ответ подсказан заранее
    'loaded-question': 'apparatus',
    'contradiction': 'apparatus',
    'tc-cheerful-summary': 'apparatus',
    'tc-task-without-material': 'apparatus',
    'task-without-material': 'apparatus',
    // 6. История под сегодняшнюю политику
    'anachronism': 'today',
    'tc-nineties-as-foil': 'today',
    'tc-modern-grudge': 'today',
    'tc-modern-authority': 'today',
    'tc-info-sovereignty-anachronism': 'today',
    // 0. Прочее — не приём, а дополнение или поправка
    'context': 'other',
    'fact-error': 'other'
  };

  /**
   * Категория аннотации: готовое поле, ровно одно значение.
   *
   * Никакого разбора тегов: категория приезжает в page_NNN.json полем
   * `category` (см. publisher._render_item). Если поля нет — это файл, собранный
   * до перехода, и такая аннотация показывается как «Прочее».
   */
  function categoryFor(ann) {
    if (!ann) return OTHER;
    var slug = ann.category;
    return (typeof slug === 'string'
      && Object.prototype.hasOwnProperty.call(TITLES, slug)) ? slug : OTHER;
  }

  /**
   * РАЗОВАЯ МИГРАЦИЯ, не рантайм: угадать категорию по тегам. Оставлено, чтобы
   * бэкфилл и тесты считали ровно то же, что scripts/remark_categories.py.
   */
  function categoryForTags(tags) {
    if (!tags || !tags.length) return OTHER;

    var weak = null;
    var found = {};
    for (var i = 0; i < tags.length; i++) {
      var tag = String(tags[i] || '').trim();
      if (!tag) continue;
      if (tag.indexOf(CAT_PREFIX) === 0) {
        var slug = tag.slice(CAT_PREFIX.length);
        if (Object.prototype.hasOwnProperty.call(TITLES, slug)) return slug;
        continue;
      }
      if (Object.prototype.hasOwnProperty.call(TAG_CATEGORIES, tag)) {
        found[TAG_CATEGORIES[tag]] = true;
      } else if (weak === null && Object.prototype.hasOwnProperty.call(WEAK_TAGS, tag)) {
        weak = WEAK_TAGS[tag];
      }
    }

    for (var j = 0; j < PRECEDENCE.length; j++) {
      if (found[PRECEDENCE[j]]) return PRECEDENCE[j];
    }
    if (found[OTHER]) return OTHER;
    return weak || OTHER;
  }

  global.RedPenCategories = {
    OTHER: OTHER,
    PRECEDENCE: PRECEDENCE,
    TITLES: TITLES,
    COLORS: COLORS,
    WEAK_TAGS: WEAK_TAGS,
    TAG_CATEGORIES: TAG_CATEGORIES,
    categoryForTags: categoryForTags,
    categoryFor: categoryFor
  };
}(typeof window !== 'undefined' ? window : this));
