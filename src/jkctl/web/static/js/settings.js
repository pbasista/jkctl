/* The configuration table, in the panels the vendor's own application uses.
 *
 * Somebody arriving from JK BMS Monitor should find a setting where they left
 * it, so the groups below are its groups and the names beside each field are
 * the datasource's own -- the same strings that application shows.
 *
 * Editing follows one rule everywhere on this page: a card's fields edit
 * together, **Apply** shows the plan and writes it, **Discard** drops it.  The
 * plan is the same one `jkctl settings set --dry-run` prints, and it is shown
 * before anything goes out, because the first write to a battery that matters
 * should not be a surprise.
 */

import { useDraft } from '/core/js/drafts.js';
import { PanelError } from '/core/js/panels.js';
import { Badge, Card, Empty, fixed } from '/core/js/ui.js';
import { html } from '/core/vendor/preact-htm.module.js';
import {
  BALANCE_LIMITS,
  BATTERY_TEMPERATURES,
  CELL_LIMITS,
  CHARGE_TARGETS,
  MOS_TEMPERATURES,
} from './bands.js';
import { RegisterCard, SETTINGS } from './panels.js';

/* The vendor's panels, in its order.  A name the board does not map simply
 * does not appear: the card is built from what came back. */
const GROUPS = [
  {
    title: 'Cell voltage protections',
    help: {
      summary: 'The setpoints that stop a charge or a discharge.',
      body: `Each protection has a release beside it — the voltage at which the unit lets
        the pack work again — and the gap between the two is what keeps it from chattering
        on and off at the limit. Ranges come from JK's own datasource, so a value its
        application would silently clamp is refused here instead.`,
    },
    names: [
      'volCellOV',
      'volCellOVPR',
      'volCellUV',
      'volCellUVPR',
      'volSysPwrOff',
      'volSmartSleep',
    ],
    // The cells themselves, drawn on the same scale: a protection is set
    // by looking at where the pack actually sits, not at a number.
    bands: [{ limits: CELL_LIMITS, reading: 'cells' }],
  },
  {
    title: 'Charging targets',
    help: {
      summary: 'Where the pack is charged to, and what counts as full.',
      body: `SOC-100% and SOC-0% are the voltages the state-of-charge reading is anchored
        to, not protections. RCV and RFV are the absorption and float voltages an inverter
        is told to use; their times are on the Ports tab, because that is where the unit
        keeps them.`,
    },
    names: ['volSOCP100', 'volSOCP0', 'volCellRCV', 'volCellRFV'],
    bands: [
      {
        limits: CHARGE_TARGETS,
        reading: 'cells',
        caption: 'Where the charge is aimed, against where the cells are',
      },
    ],
  },
  {
    title: 'Current and its timing',
    help: {
      summary: 'Continuous currents, and how long an overload is tolerated.',
      body: `A delay is how long the unit waits before tripping, and a release time is how
        long it stays tripped. The short-circuit delay is in microseconds because that
        protection is a hardware comparator, not a poll loop.`,
    },
    names: [
      'timBatCOC',
      'timBatCOCPDly',
      'timBatCOCPRDly',
      'timBatDcOC',
      'timBatDcOCPDly',
      'timBatDcOCPRDly',
      'timBatSCPRDly',
      'scpDelay',
      'dischrgPreChrgT',
    ],
  },
  {
    title: 'Temperature protections',
    help: {
      summary: 'The bands the pack has to stay inside, and the heater.',
      body: `Charging a lithium cell below freezing plates lithium onto the anode and does
        not undo itself, which is why the charge under-temperature protection matters more
        than the others. The heater's two setpoints only do anything on a board with a
        heating output.`,
    },
    names: [
      'tmpBatCOT',
      'tmpBatCOTPR',
      'tmpBatCUT',
      'tmpBatCUTPR',
      'tmpBatDcOT',
      'tmpBatDcOTPR',
      'tmpBatDCHUT',
      'tmpBatDCHUTPR',
      'tmpMosOT',
      'tmpMosOTPR',
      'tmpStartHeating',
      'tmpStopHeating',
    ],
    // The MOSFETs run hotter than the cells by design, so they are their
    // own scale: one band with both would put a healthy MOS outside a band
    // that has nothing to do with it.
    bands: [
      { limits: BATTERY_TEMPERATURES, reading: 'probes' },
      { limits: MOS_TEMPERATURES, reading: 'mos' },
    ],
  },
  {
    title: 'Capacity and cells',
    help: {
      summary: 'How many cells there are, and how much they hold.',
      body: `The cell count has to match the cells actually wired: the unit protects the
        cells it knows about. The design capacity is what the state-of-charge reading is
        scaled against, so a wrong one makes every percentage wrong.`,
    },
    names: ['cellCount', 'capBatCell', 'currentRange'],
  },
  {
    title: 'Balancing',
    help: {
      summary: 'When the balancer runs, and how hard.',
      body: `The trigger is the spread at which balancing starts; the start voltage is how
        far up the charge curve it waits before doing anything, since cell voltages only
        separate meaningfully near the top.`,
    },
    names: ['volBalanTrig', 'volStartBalan', 'curBalanMax'],
    bands: [
      {
        limits: BALANCE_LIMITS,
        reading: 'cells',
        caption: 'Where balancing starts, against where the cells are',
      },
    ],
  },
  {
    title: 'Main switches',
    help: {
      summary: 'Charging, discharging and balancing.',
      body: `Three ordinary settings registers rather than action slots, so each is a
        single write that takes effect at once. They are also on the dashboard, which is
        where you would reach for them while looking at a pack.`,
    },
    names: ['batChargeEn', 'batDischargeEn', 'balanEn'],
  },
  {
    title: 'Sleep and the bus',
    help: {
      summary: 'Smart sleep, and the address this board answers on.',
      body: `Changing the device address changes which \`--id\` reaches this unit. On most
        boards the DIP switches select the address and this register follows them, so check
        the switches before relying on it.`,
    },
    names: ['timeSmartSleep', 'devAddr'],
  },
];

/* The sixteen settings JK multiplexes into one register.
 *
 * They were written the moment each was clicked, the one card on this tab
 * without an Apply, on the reasoning that each is a read-modify-write of
 * the whole word and two held together would be two writes of it anyway.
 * They are -- but that is the writer's business, not the page's: the
 * writer in app.js sends them one bit at a time, each against a fresh read
 * of the word, and the card is edited like every other. */
export const SWITCHES = 'switches';

export function switchKey(bit) {
  return `bit${bit}`;
}

function SwitchCard({ switches, readOnly }) {
  const draft = useDraft(SWITCHES);
  if (!switches) return null;
  return html`<${Card}
    title="Multiplexed switches"
    width="wide"
    draft=${draft}
    help=${{
      summary: 'Sixteen unrelated settings in one register.',
      body: html`JK keeps these as the bits of one word, so each is written by reading the
        word, changing its bit and writing the word back. Applying several sends them one
        after another in that way.`,
    }}
  >
    ${switches.map((row) => {
      const key = switchKey(row.bit);
      const held = draft.has(key);
      const on = held ? draft.edits[key] : row.on;
      return html`<div class=${held ? 'row pending' : 'row'} key=${row.bit}>
        <div class="k" title=${`bit ${row.bit} of the switch word`}>${row.name}</div>
        <div class="v">
          <label class=${held ? 'toggle pending' : 'toggle'}>
            <span class="muted">${on ? 'on' : 'off'}</span>
            <input
              type="checkbox"
              checked=${on}
              disabled=${readOnly}
              onChange=${(e) => {
                /* Flipped back to what the board holds is not an edit. */
                if (e.target.checked === row.on) draft.drop(key);
                else draft.set(key, e.target.checked);
              }}
            />
          </label>
        </div>
      </div>`;
    })}
  <//>`;
}

export function SettingsTab({ registers, runtime, switches, error, readOnly, onReload }) {
  if (error) return html`<${PanelError} error=${error} onRetry=${onReload} />`;
  if (!registers) return html`<${Empty}>Reading the configuration…<//>`;

  const grouped = new Set(GROUPS.flatMap((g) => g.names));
  // Arrays have their own tab and the switch word has its own card below: a
  // sixteen-bit word shown as the number 16 is not a setting anybody can use.
  const rest = registers.filter(
    (r) => !grouped.has(r.name) && r.kind !== 'array' && r.kind !== 'bits',
  );
  /* Said only when it is worth saying: a register that did not answer is
   * one whose row says "not mapped on this board", and a page of them is a
   * board that is not what the datasource expected. */
  const silent = registers.filter((r) => !r.answered).length;

  return html`<div>
    ${silent
      ? html`<div class="notice warn">
          ${silent} of ${registers.length} settings did not answer when read, and are shown
          as not mapped on this board.
        </div>`
      : null}
    <div class="grid">
      ${GROUPS.map(
        (group) => html`<${RegisterCard}
          key=${group.title}
          title=${group.title}
          help=${group.help}
          names=${group.names}
          registers=${registers}
          disabled=${readOnly}
          bands=${group.bands}
          runtime=${runtime}
        />`,
      )}
      ${rest.length
        ? html`<${RegisterCard}
            title="Everything else"
            help=${{
              summary: 'Settings the vendor panels do not group.',
              body: 'The register browser shows these with their raw bytes and addresses.',
            }}
            names=${rest.map((r) => r.name)}
            registers=${registers}
            disabled=${readOnly}
            width="wide"
          />`
        : null}
      <${SwitchCard} switches=${switches} readOnly=${readOnly} />
    </div>
  </div>`;
}

/* --- cells and balancing ------------------------------------------------- */

/* The connection-wire resistances: thirty-two values in one register, which
 * JK's own application edits a box at a time and which the CLI would
 * otherwise want all thirty-two of.  Each is its own word on the wire, so one
 * can be written without the other thirty-one. */
export function CellsTab({ registers, runtime, readOnly }) {
  const draft = useDraft(SETTINGS, { names: ['cellConWireRes'] });
  if (!registers) return html`<${Empty}>Reading the configuration…<//>`;
  const wires = registers.find((r) => r.name === 'cellConWireRes');
  const measured = runtime?.cells || [];
  const count = registers.find((r) => r.name === 'cellCount')?.value ?? measured.length;

  return html`<div>
    <div class="grid">
      <${RegisterCard}
        title="Balancing"
        names=${['balanEn', 'volBalanTrig', 'volStartBalan', 'curBalanMax']}
        registers=${registers}
        disabled=${readOnly}
        runtime=${runtime}
        bands=${[
          {
            limits: BALANCE_LIMITS,
            reading: 'cells',
            caption: 'Where balancing starts, against where the cells are',
          },
        ]}
      />
      <${Card}
        title="Connection wire resistance"
        width="wide"
        draft=${draft}
        badge=${wires?.answered ? html`<${Badge}>${wires.count} values<//>` : null}
        help=${{
          summary: 'What each sense wire is configured as, beside what the unit measures.',
          body: html`The configured value is used to correct each cell's reading for the drop
            along its own wire; the measured one is what the balancer sees. A measured value
            far off its neighbours' is usually a crimp rather than a cell.`,
        }}
      >
        ${!wires?.answered
          ? html`<${Empty}>This board did not answer for the wire resistances.<//>`
          : html`<div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Cell</th>
                    <th class="num">Configured</th>
                    <th class="num">Measured</th>
                    <th class="num">Cell voltage</th>
                  </tr>
                </thead>
                <tbody>
                  ${Array.from({ length: wires.count }, (_, i) => i)
                    .filter((i) => i < Math.max(count, measured.length))
                    .map((i) => {
                      const name = `cellConWireRes[${i}]`;
                      const held = draft.edits[name];
                      const current = wires.value?.[i];
                      const cell = measured[i];
                      return html`<tr key=${i}>
                        <td>${i + 1}</td>
                        <td class="num">
                          <input
                            type="number"
                            step="0.001"
                            class=${held !== undefined ? 'num pending' : 'num'}
                            style="width:6.5rem"
                            value=${held !== undefined ? held : (current ?? '')}
                            disabled=${readOnly}
                            onInput=${(e) => draft.set(name, e.target.value)}
                          />
                        </td>
                        <td class="num">${fixed(cell?.resistance, 3)}</td>
                        <td class="num">${fixed(cell?.voltage, 3)} V</td>
                      </tr>`;
                    })}
                </tbody>
              </table>
            </div>`}
      <//>
    </div>
  </div>`;
}
