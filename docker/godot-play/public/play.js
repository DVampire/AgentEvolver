import RFB from './novnc/core/rfb.js';

const screen = document.querySelector('#screen');
const status = document.querySelector('#status');
const button = document.querySelector('#connect');
let rfb;
fetch('./game.json').then(r => r.json()).then(game => {
  document.querySelector('#title').textContent = game.title;
  document.title = game.title;
}).catch(() => {});
button.addEventListener('click', () => {
  if (rfb) rfb.disconnect();
  // Resolve against the public release path, including /s/<site>--r<n>/.
  const url = new URL('./websockify', location.href);
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const client = new RFB(screen, url.href, { shared: true });
  rfb = client;
  client.scaleViewport = true;
  client.resizeSession = false;
  client.viewOnly = false;
  status.textContent = 'Connecting…';
  client.addEventListener('connect', () => {
    if (rfb !== client) return;
    status.textContent = 'Connected'; button.textContent = 'Reconnect'; client.focus();
  });
  client.addEventListener('disconnect', () => {
    if (rfb !== client) return;
    status.textContent = 'Disconnected — reconnect to try again'; button.textContent = 'Reconnect';
  });
});
document.querySelector('#fullscreen').addEventListener('click', () => screen.requestFullscreen());
