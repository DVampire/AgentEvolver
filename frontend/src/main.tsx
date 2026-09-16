import { createRoot } from 'react-dom/client';

import { App } from './App';
import './style/theme.css';
import './style/tailwind.css';
import './style/index.css';

// Apply the saved preference before mounting, including portals rendered outside the shell.
document.documentElement.classList.toggle('dark', localStorage.getItem('agentevolver.theme') !== 'light');
createRoot(document.getElementById('root')!).render(<App />);
