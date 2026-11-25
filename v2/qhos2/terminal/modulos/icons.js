const paths = {
  chart: '<path d="M4 4v16h16M7 14l4-4 4 3 5-7"/>',
  search: '<circle cx="10.8" cy="10.8" r="6.4"/><path d="m15.4 15.4 4.7 4.7"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-2 7-2 9h16c0-2-2-2-2-9ZM10 21h4"/>',
  'bell-plus': '<path d="M15 3.8A6 6 0 0 0 6 9c0 6-2 6-2 8h16c0-2-2-2-2-6M10 21h4M19 2v6M16 5h6"/>',
  crosshair: '<circle cx="12" cy="12" r="7"/><path d="M12 2v5M12 17v5M2 12h5M17 12h5"/>',
  trend: '<path d="m4 18 16-12M15 6h5v5"/><circle cx="4" cy="18" r="1.5"/>',
  horizontal: '<path d="M3 12h18"/><circle cx="7" cy="12" r="1.6" fill="currentColor" stroke="none"/>',
  indicators: '<path d="M4 18V6M10 18V11M16 18V3M22 18V8"/>',
  note: '<path d="M14 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-9M8 16l1-4L18 3l3 3-9 9-4 1ZM16 5l3 3"/>',
  trash: '<path d="M3 6h18M8 6V3h8v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.4 8.4a2.7 2.7 0 0 1 5.2 1c0 2-2.6 2.2-2.6 4.1M12 17h.01"/>',
  settings: '<path d="m9.8 3-.6 2.1-2 .9-2-.6L3 9.2l1.5 1.5v2.6L3 14.8l2.2 3.8 2-.6 2 .9.6 2.1h4.4l.6-2.1 2-.9 2 .6 2.2-3.8-1.5-1.5v-2.6L21 9.2l-2.2-3.8-2 .6-2-.9-.6-2.1z"/><circle cx="12" cy="12" r="3"/>',
  'chevron-down': '<path d="m6 9 6 6 6-6"/>',
  candles: '<path d="M7 3v4M7 16v5M17 3v8M17 18v3"/><rect x="4" y="7" width="6" height="9" rx=".7"/><rect x="14" y="11" width="6" height="7" rx=".7"/>',
  replay: '<path d="m3 8 4-4M3 8h5M3 8a9 9 0 1 1 .5 9"/><path d="m10 8 6 4-6 4z"/>',
  camera: '<path d="M8 6l1.5-3h5L16 6h4a2 2 0 0 1 2 2v11H2V8a2 2 0 0 1 2-2z"/><circle cx="12" cy="12.5" r="3.5"/>',
  expand: '<path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5"/>',
  minus: '<path d="M5 12h14"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  reset: '<path d="M4 10a8 8 0 1 1 1.5 7M4 4v6h6"/>',
  pause: '<path d="M8 5v14M16 5v14" stroke-width="3"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  wallet: '<path d="M20 7H5a2 2 0 0 1 0-4h13v4M3 5v14a2 2 0 0 0 2 2h15V7M20 11h-5v5h5"/><path d="M17 13.5h.01"/>',
  'arrow-right': '<path d="M4 12h16M14 6l6 6-6 6"/>',
  star: '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9z"/>',
  shield: '<path d="m12 2 8 3v6c0 5-4.5 9-8 11-3.5-2-8-6-8-11V5z"/><path d="m8 12 3 3 5-6"/>',
  sort: '<path d="M8 4v16M4 8l4-4 4 4M16 20V4M12 16l4 4 4-4"/>',
  command: '<path d="M8 8H5.5A2.5 2.5 0 1 1 8 5.5V18.5A2.5 2.5 0 1 1 5.5 16H18.5A2.5 2.5 0 1 1 16 18.5V5.5A2.5 2.5 0 1 1 18.5 8H8Z"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
  line: '<path d="m3 17 5-5 4 3 4-8 5-3"/>',
  area: '<path d="m3 17 5-5 4 3 4-8 5-3v17H3z"/>',
  play: '<path d="m8 4 12 8-12 8z"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5M4 16v5h16v-5"/>',
  'arrow-up-right': '<path d="M6 18 18 6M7 6h11v11"/>',
  'arrow-down-right': '<path d="m6 6 12 12M7 18h11V7"/>',
  bookmark: '<path d="M6 3h12v18l-6-4-6 4z"/>',
  portfolio: '<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V3h8v4M3 12l9 3 9-3M10 13v3h4v-3"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8zM2 12l10 5 10-5M2 16l10 5 10-5"/>',
  'check-circle': '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
};

function escapeAttribute(value) {
  return String(value).replace(/[&"<>']/g, character => ({ '&': '&amp;', '"': '&quot;', '<': '&lt;', '>': '&gt;', "'": '&#39;' })[character]);
}

export function icon(name, cls = '') {
  const geometry = Object.hasOwn(paths, name) ? paths[name] : paths.info;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"${cls ? ` class="${escapeAttribute(cls)}"` : ''}>${geometry}</svg>`;
}

export function hydrateIcons(root = document) {
  const elements = Array.from(root.querySelectorAll('i[data-icon]'));
  if (root.matches?.('i[data-icon]')) elements.unshift(root);
  for (const element of elements) {
    const name = element.getAttribute('data-icon');
    if (element.dataset.renderedIcon === name && element.firstElementChild?.tagName.toLowerCase() === 'svg') continue;
    element.innerHTML = icon(name);
    element.dataset.renderedIcon = name;
  }
}
