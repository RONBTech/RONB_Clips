/* Shared, live state: import these objects, never copy their contents. */
/* H is filled in by main.js at startup, so feature modules need no circular imports. */
export const H = {};

/* ============================================================ state */
export const S = {status:{styles:{}, layouts:{}}, jobs:[], creators:[], accounts:[], overview:null, settings:null,
           view:"overview", ctab:"clips"};
export const D = {jid:null, cid:null, sig:""};
export const creatorId = () => S.view.startsWith("c:") ? S.view.slice(2) : null;
export const creator = () => S.creators.find(c => c.id === creatorId());
export const jobOf = id => S.jobs.find(j => j.id === id);
export const sel = () => (S.sel = S.sel || new Set());
export const DRAFTS = {};
