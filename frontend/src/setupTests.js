// Jest setup for React Testing Library.
// Imported automatically by CRA before each test file.
import '@testing-library/jest-dom';

// Polyfill TextEncoder for react-router-dom + axios on jsdom.
import { TextEncoder, TextDecoder } from 'util';
if (typeof global.TextEncoder === 'undefined') global.TextEncoder = TextEncoder;
if (typeof global.TextDecoder === 'undefined') global.TextDecoder = TextDecoder;

// Silence noisy console.error coming from React 19 internal warnings
// during act() flushes — keep real errors visible.
const _origError = console.error;
console.error = (...args) => {
  const msg = String(args[0] || '');
  if (msg.includes('not wrapped in act(')) return;
  if (msg.includes('Warning: ReactDOM.render')) return;
  _origError(...args);
};
