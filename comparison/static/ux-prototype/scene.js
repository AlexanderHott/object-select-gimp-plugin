// Original vector fixture. Every selectable region has a fixed mask.
const fixture = {
  foliage: {name: 'Leaves & stems', group: 'plant', color: 'url(#leaf)', d: 'M230 358 L221 247 C169 266 121 224 130 175 C188 173 220 205 224 237 L230 199 C181 171 180 123 203 94 C247 113 252 155 234 195 L238 250 C239 197 277 163 329 177 C327 224 294 255 241 256 L237 294 C263 257 303 254 336 276 C315 316 275 329 235 304 L238 358 Z M219 291 C170 300 141 279 140 246 C182 232 212 254 219 291 Z'},
  pot: {name: 'Plant pot', group: 'plant', color: 'url(#clay)', d: 'M167 347 Q232 334 295 347 L282 448 Q230 476 179 448 Z'},
  strap: {name: 'Camera strap', group: 'strap', color: '#74523e', d: 'M409 349 C365 350 339 398 342 453 C344 522 405 553 484 539 L492 522 C414 537 362 512 360 454 C358 404 377 370 410 367 Z'},
  body: {name: 'Camera body', group: 'camera', color: 'url(#camera)', d: 'M405 309 L466 309 L482 282 L565 282 L583 309 L650 309 Q676 309 676 335 L676 463 Q676 484 653 484 L412 484 Q390 484 390 462 L390 334 Q390 309 405 309 Z M614 398 A70 70 0 1 0 474 398 A70 70 0 1 0 614 398 Z'},
  lens: {name: 'Camera lens', group: 'camera', color: 'url(#lens)', d: 'M614 398 A70 70 0 1 0 474 398 A70 70 0 1 0 614 398 Z'},
  mug: {name: 'Coffee cup', group: 'mug', color: 'url(#ceramic)', d: 'M717 368 Q763 350 810 368 L810 385 C875 367 881 452 809 451 L806 485 Q763 507 724 485 Z M812 404 L811 433 C849 435 853 395 812 404 Z'},
};
const groupNames = {plant: 'Plant', camera: 'Camera', strap: 'Camera strap', mug: 'Coffee cup'};
const fixtureDefs = `<linearGradient id="wall" x2="0.8" y2="1"><stop stop-color="#eee7d8"/><stop offset="1" stop-color="#c9c7ad"/></linearGradient>
<linearGradient id="clay"><stop stop-color="#ac563b"/><stop offset=".45" stop-color="#d48861"/><stop offset="1" stop-color="#a95b3f"/></linearGradient>
<linearGradient id="leaf" x2="1" y2="1"><stop stop-color="#526850"/><stop offset="1" stop-color="#283f35"/></linearGradient>
<linearGradient id="camera" x2="0" y2="1"><stop stop-color="#565a57"/><stop offset=".3" stop-color="#323b38"/><stop offset="1" stop-color="#182622"/></linearGradient>
<radialGradient id="lens"><stop stop-color="#426467"/><stop offset=".34" stop-color="#152b2d"/><stop offset=".56" stop-color="#2d4444"/><stop offset=".64" stop-color="#131e1c"/><stop offset=".88" stop-color="#37423c"/><stop offset="1" stop-color="#101d18"/></radialGradient>
<linearGradient id="ceramic"><stop stop-color="#e1c77f"/><stop offset=".45" stop-color="#f2dda0"/><stop offset="1" stop-color="#bfab71"/></linearGradient>
<pattern id="checker" width="24" height="24" patternUnits="userSpaceOnUse"><rect width="24" height="24" fill="#eee"/><path d="M0 0H12V12H0ZM12 12H24V24H12Z" fill="#d1d1d1"/></pattern>
<pattern id="hatch" width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(35)"><rect width="10" height="10" fill="#86b9ff" fill-opacity=".25"/><path d="M0 0V10" stroke="#a8d2ff" stroke-width="3"/></pattern>
<filter id="shadow"><feGaussianBlur stdDeviation="12"/></filter>`;
function fixturePath(id, attrs = '') { return `<path d="${fixture[id].d}" fill-rule="evenodd" ${attrs}/>`; }
function sceneArtwork() {
  return `<rect width="960" height="640" fill="url(#wall)"/><path d="M0 414L960 372V640H0Z" fill="#c9aa83"/><path d="M0 418L960 377" stroke="#b69a76" opacity=".6"/>
  <g fill="#554b34" opacity=".2" filter="url(#shadow)"><ellipse cx="240" cy="461" rx="94" ry="20"/><ellipse cx="551" cy="489" rx="148" ry="20"/><ellipse cx="781" cy="496" rx="68" ry="16"/></g>
  ${Object.entries(fixture).map(([id, f]) => fixturePath(id, `fill="${f.color}"`)).join('')}
  <g pointer-events="none"><ellipse cx="231" cy="347" rx="64" ry="13" fill="#9b573e"/><ellipse cx="231" cy="345" rx="54" ry="8" fill="#4c4030"/><path d="M232 344L234 320" stroke="#334c37" stroke-width="6"/>
  <path d="M400 333H663" stroke="#9a9e8b" stroke-width="4"/><rect x="409" y="346" width="39" height="19" rx="3" fill="#b1b4a1"/><rect x="490" y="294" width="46" height="20" rx="3" fill="#152520"/><circle cx="544" cy="398" r="51" fill="none" stroke="#6d796c" stroke-width="2"/><circle cx="544" cy="398" r="30" fill="none" stroke="#77918b" stroke-opacity=".4" stroke-width="3"/><ellipse cx="530" cy="382" rx="11" ry="8" fill="#b8d6c5" opacity=".28"/><path d="M631 349V454" stroke="#657064" stroke-width="3"/><text x="407" y="462" fill="#b5b9a4" font-size="10" letter-spacing="2" font-family="sans-serif">ANALOG</text>
  <ellipse cx="763" cy="368" rx="46" ry="13" fill="#f4dfa4"/><ellipse cx="763" cy="369" rx="36" ry="8" fill="#5c4230"/><path d="M741 398L744 471" stroke="#fff0bc" stroke-width="5" opacity=".5"/></g>`;
}
