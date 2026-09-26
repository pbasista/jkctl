/* A register, edited.
 *
 * The register describes itself -- unit, decimals, range, factory default,
 * enum labels, R/W -- because the server sends that with every field.  So
 * one component covers every setting the datasource knows about, and a
 * firmware that adds a register gets an editor for free.
 *
 * The editing model is the CLI's: a card's fields edit together, Apply
 * shows the plan and sends what changed, and Discard drops it.  A
 * half-typed limit never reaches a battery.  The draft, the plan and the
 * Apply are the shared ones in /core/js/drafts.js; what is here is the row
 * and the card of them.
 */

import { useDraft } from '/core/js/drafts.js';
import { Card, Select } from '/core/js/ui.js';
import { html, useEffect } from '/core/vendor/preact-htm.module.js';
import { bandNames, CardBands } from './bands.js';

/* Every setting a board holds is sent the one way, whichever card it was
 * changed in -- see `offerWriter` in app.js. */
export const SETTINGS = 'settings';

function RegisterRow({ reg, draft, disabled, options }) {
  const held = draft.edits[reg.name];
  const changed = held !== undefined;
  const value = changed ? held : reg.value;
  /* A board that turned down a write to this register whatever it was sent
   * (the server's `refused`) gets the value shown and the reason beside it,
   * not an editor whose every change would come back refused again. */
  const readOnly = disabled || !reg.writable || !!reg.refused;
  /* An edit made before the board said so is an edit nothing can send. */
  useEffect(() => {
    if (reg.refused && changed) draft.drop(reg.name);
  }, [reg.refused, changed]);


  /* A trigger source is a number indexing a list the server ships with the
   * ports document rather than one the datasource carries, so a caller can
   * hand this row the options for it. */
  const entries = options || (reg.options || []).map((o) => [o.value, o.label]);

  let control;
  if (!reg.answered) {
    control = html`<span class="muted">not mapped on this board</span>`;
  } else if (reg.refused) {
    control = html`<span class="wrap" style="justify-content:flex-end" title=${reg.refused}>
      <span>${labelFor(entries, reg.value) ?? reg.text}</span>
      <span class="badge warn">board refuses writes</span>
    </span>`;
  } else if (readOnly) {
    control = html`<span>${labelFor(entries, reg.value) ?? reg.text}</span>`;
  } else if (reg.kind === 'flag') {
    control = html`<label class=${changed ? 'toggle pending' : 'toggle'}>
      <span class="muted">${Number(value) ? 'on' : 'off'}</span>
      <input
        type="checkbox"
        checked=${!!Number(value)}
        onInput=${(e) => {
          /* Flipped back to what the board holds is no longer an edit. */
          const next = e.target.checked ? '1' : '0';
          if (Number(next) === Number(reg.value)) draft.drop(reg.name);
          else draft.set(reg.name, next);
        }}
      />
    </label>`;
  } else if (options || reg.kind === 'enum') {
    control = html`<${Select}
      value=${value}
      pending=${changed}
      entries=${entries}
      onChange=${(v) => draft.set(reg.name, v)}
    />`;
  } else if (reg.kind === 'text') {
    control = html`<input
      type="text"
      class=${changed ? 'pending' : ''}
      value=${value ?? ''}
      onInput=${(e) => draft.set(reg.name, e.target.value)}
    />`;
  } else {
    control = html`<span class="wrap" style="justify-content:flex-end">
      <input
        type="number"
        class=${changed ? 'num pending' : 'num'}
        value=${value ?? ''}
        step=${reg.decimals ? (10 ** -reg.decimals).toFixed(reg.decimals) : 1}
        min=${reg.minimum ?? undefined}
        max=${reg.maximum ?? undefined}
        onInput=${(e) => draft.set(reg.name, e.target.value)}
      />
      ${reg.unit ? html`<span class="muted">${reg.unit}</span>` : null}
    </span>`;
  }

  return html`<div class=${changed ? 'row pending' : 'row'}>
    <div class="k hinted" title=${hintOf(reg)}>${reg.label || reg.name}</div>
    <div class="v" title=${boundsOf(reg)}>${control}</div>
  </div>`;
}

/* What a register is, for the tooltip on its name: what it means, and
 * where to find it -- JK's own label, the one its application shows, and
 * the key the CLI and the register browser use. */
export function hintOf(reg) {
  const where =
    reg.vendor && reg.vendor !== reg.label ? `JK: ${reg.vendor} [${reg.name}]` : `[${reg.name}]`;
  return reg.description ? `${reg.description}\n${where}` : where;
}

/* What it may be, for the tooltip on its value. */
export function boundsOf(reg) {
  const unit = reg.unit ? ` ${reg.unit}` : '';
  return [
    reg.minimum !== null && reg.maximum !== null
      ? `allowed ${reg.minimum}${unit} to ${reg.maximum}${unit}`
      : null,
    reg.default !== null && reg.default !== undefined ? `default ${reg.default}${unit}` : null,
  ]
    .filter(Boolean)
    .join(' · ');
}

/* The word an option table gives a value, for a field being shown rather than
 * edited. */
function labelFor(entries, value) {
  const found = (entries || []).find(([v]) => String(v) === String(value));
  return found ? found[1] : null;
}

/* A card of registers, chosen by name.  The groups are the vendor's own
 * panels, so somebody arriving from JK BMS Monitor finds the setting where
 * they left it.
 *
 * Every card of settings is a view of the one draft of them: it counts the
 * edits that are its own -- its fields, and the setpoints its bands can
 * drag -- and applies or discards just those from its title, while the
 * header counts and sends all of them.  A setting on two cards (a
 * protection drawn on the dashboard and listed here) is one edit shown in
 * both. */
export function RegisterCard({
  title,
  help,
  names,
  registers,
  disabled,
  width,
  bands,
  runtime,
  caveats,
  options,
}) {
  const draft = useDraft(SETTINGS, { names: [...names, ...bandNames(bands)] });
  const byName = new Map(registers.map((r) => [r.name, r]));
  const rows = names.map((n) => byName.get(n)).filter(Boolean);
  if (!rows.length) return null;
  return html`<${Card} title=${title} help=${help} width=${width} draft=${draft}>
    <${CardBands}
      bands=${bands}
      registers=${registers}
      runtime=${runtime}
      draft=${draft}
      disabled=${disabled}
    />
    ${rows.map(
      (reg) => html`<${RegisterRow}
        key=${reg.name}
        reg=${reg}
        draft=${draft}
        disabled=${disabled}
        options=${options?.[reg.name]}
      />`,
    )}
    ${caveats}
  <//>`;
}
