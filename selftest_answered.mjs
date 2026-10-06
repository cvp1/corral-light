/* A permission answer says who gave it:
 *
 *   1. Only the wall's and the terminal's answers read "you chose".
 *   2. A script's answer says so and says "not you".
 *   3. An answer with no label, or an unknown one (every answer recorded
 *      before labels existed), never claims "you".
 *   4. Both places that show an answer (the card and the transcript line) use
 *      answeredBy, so neither can drift back to an unconditional "you chose".
 *
 * Run: node selftest_answered.mjs   (exit 0 = pass)
 */
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('static/app.js', new URL('./', import.meta.url)), 'utf8');

let bad = 0;
const check = (ok, why) => { if (!ok) { console.error(`FAIL: ${why}`); bad++; } };

function fn(name) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`no function ${name} in app.js`);
  let depth = 0;
  for (let j = src.indexOf('{', start); j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}' && --depth === 0) return src.slice(start, j + 1);
  }
  throw new Error(`unbalanced braces in ${name}`);
}

const answeredBy = new Function(`${fn('answeredBy')}; return answeredBy;`)();
const said = via => answeredBy({ optionId: 'reject', via });

check(said('wall') === 'you chose “reject”', `wall: ${said('wall')}`);
check(said('terminal').startsWith('you chose') && said('terminal').includes('terminal'),
      `terminal: ${said('terminal')}`);
check(said('script').includes('a script chose') && said('script').includes('not you'),
      `script: ${said('script')}`);
for (const via of [undefined, null, '', 'operator']) {
  check(!/\byou\b/.test(said(via)) && said(via).includes('not recorded'),
        `unlabelled (${via}) must not claim you: ${said(via)}`);
}

check(!/you chose/.test(src.replace(fn('answeredBy'), '')),
      'only answeredBy may write "you chose"');
check((src.match(/answeredBy\(/g) || []).length >= 3,
      'the card and the transcript line must both call answeredBy');

if (bad) { console.error(`${bad} failure(s)`); process.exit(1); }
console.log('selftest_answered: ok');
