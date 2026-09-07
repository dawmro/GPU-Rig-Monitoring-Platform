# Reusable partials

This directory contains reusable Django template partials. Each partial
encapsulates a single repeated UI pattern from the project's templates.

## Available partials

### `_form_input.html`
Labeled form input (label + input + optional help/error text). Replaces
~6 lines of repetitive HTML with a single `{% include %}` call.

Usage:
```django
{% include "partials/_form_input.html" with
   name="email"
   label="Email"
   type="email"
   required=True
   value=form.email.value
   help_text="We will never share your email." %}
```

### `_chart_card.html`
Chart card with title, timeframe label, and `<canvas>`. Replaces the
8-line `<div class="grm-chart-card">` boilerplate repeated 21 times in
`rig_detail.html`.

Usage:
```django
{% include "partials/_chart_card.html" with
   canvas_id="chartGpuTemp"
   title="GPU Temperature" %}
```

## Why no `_card.html`?

A `_card.html` partial (the standard `.grm-card` panel) would save one
`<div>` per card, but Django's `{% include %}` does not support content
blocks without a third-party package (`django-slots`). Each card in
`_metrics_cards.html` has 30-50 lines of unique content, so the
content-as-context-variable pattern would be more cumbersome than the
direct HTML. The `grm-card` CSS class is already the single source of
truth for card styling — the partial layer would add indirection
without removing duplication.

If a future need arises (e.g. a "compact" vs "expanded" card variant),
we can revisit this with `django-slots` or a custom template tag.
