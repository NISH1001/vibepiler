import { readFileSync } from 'fs';

console.log('polyfill loaded', readFileSync('/dev/null').length);
