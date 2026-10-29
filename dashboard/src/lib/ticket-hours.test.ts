import assert from 'node:assert/strict';
import { test } from 'node:test';
import { discordText, fromGrid, scheduleText, toGrid } from './sections/tickets/hours.ts';

test('the grid round-trips to the bot text, midnight as 24:00', () => {
	const s = [[[540, 1020]], [], [], [], [[540, 720], [780, 1440]], [], []];
	assert.equal(scheduleText(s), 'mon 09:00-17:00; fri 09:00-12:00 13:00-24:00');
	assert.deepEqual(toGrid(s)[4], [['09:00', '12:00'], ['13:00', '00:00']]);
	assert.equal(fromGrid(toGrid(null)), '');
	const week = toGrid(Array.from({ length: 7 }, () => [[600, 1440]]));
	assert.deepEqual(week[6], [['10:00', '00:00']]);
	assert.equal(fromGrid(week), ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map((d) => `${d} 10:00-24:00`).join('; '));
});

test('the preview escapes HTML and renders Discord markup', () => {
	const now = Date.UTC(2026, 0, 1);
	const html = discordText(`Hi <@1> <b>x</b> back <t:${now / 1000 + 7200}:R>\n> **note**`, { '1': 'aria' }, now);
	assert.ok(html.startsWith('Hi <span class="mention">@aria</span> &lt;b&gt;x&lt;/b&gt; back in 2 hours'), html);
	assert.ok(html.endsWith('<blockquote><b>note</b></blockquote>'), html);
});
