/* Talking to the jkctl server: every endpoint this page knows about.
 *
 * The transport is shared -- the UI header, the two kinds of failure, the
 * event stream and its retry all live in /core/js/api.js, which knows
 * nothing about this program beyond the name it says when the server stops
 * answering.  What is here is the list: one named function per endpoint,
 * so a panel asks for `settings(id)` rather than assembling a URL, and the
 * one place a path is spelled is this file.
 */

import { configure, download, get, post, query, upload } from '/core/js/api.js';

configure({ name: 'jkctl' });

export const state = () => get('/api/state');
export const ports = () => get('/api/ports');
export const clients = () => get('/api/clients');
export const bank = () => get('/api/bank');
export const dashboard = (id) => get(`/api/dashboard${query({ id })}`);
export const registers = (id, table) => get(`/api/registers${query({ id, table })}`);
export const settings = (id) => get(`/api/settings${query({ id })}`);
export const switches = (id) => get(`/api/switches${query({ id })}`);
export const alarms = (id) => get(`/api/alarms${query({ id })}`);
export const doctor = (id) => get(`/api/doctor${query({ id })}`);
export const protocols = (id) => get(`/api/protocols${query({ id })}`);
export const samples = (id) => get(`/api/samples${query({ id })}`);
export const history = (id, base) => get(`/api/history${query({ id, base })}`);
export const historyDump = (bytes) => upload('/api/history/dump', bytes);
export const logCodes = () => get('/api/log-codes');
export const library = (id, dir) => get(`/api/firmware/library${query({ id, dir })}`);

export const choosePort = (doc) => post('/api/port', doc);
export const rescan = () => post('/api/scan', {});
export const link = (action) => post('/api/link', { action });
export const live = (on, interval) => post('/api/live', { live: on, interval });
export const writeSettings = (doc) => post('/api/settings', doc);
export const writeInfo = (doc) => post('/api/settings', { ...doc, table: '03' });
export const writeSwitch = (doc) => post('/api/switch', doc);
export const writeControl = (doc) => post('/api/control', doc);
export const writeProtocol = (doc) => post('/api/protocol', doc);
export const runAction = (doc) => post('/api/action', doc);
export const importSettings = (doc) => post('/api/import', doc);
export const checkFirmware = (id, bytes) =>
  upload(`/api/firmware/check${query({ id })}`, bytes);
export const flashFirmware = (id, bytes, force) =>
  upload(`/api/firmware/flash${query({ id, force: force ? 1 : '' })}`, bytes);
export const focus = () => post('/api/focus', {});

/* The two downloads: a unit's settings, and the serial trace.  Both are
 * files the server names -- the unit or the moment is in the filename --
 * so both go through the shared `download`, which reads that name back off
 * the reply rather than making one up here. */
export const exportSettings = (id) =>
  download('/api/export', { params: { id }, fallback: `jk-${id}-settings.json` });
export const trace = (action, extra = {}) => post('/api/trace', { action, ...extra });
export const traceReport = () => download('/api/trace', { fallback: 'jkctl-trace.txt' });
