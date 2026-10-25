// 16×16 stroke icons from the Calm prototype.
export const ICONS = {
	overview: 'M2 9h3l2-5 2 9 2-4h3',
	slowmode: 'M3 11a5 5 0 1 1 10 0M8 11l2.5-3.5',
	logging: 'M3 4h10M3 8h10M3 12h6',
	cases: 'M8 2l5 2v4c0 3-2.2 5-5 6-2.8-1-5-3-5-6V4z',
	stats: 'M3 13V8M8 13V3M13 13V6',
	modules: 'M3 3h4v4H3zM9 3h4v4H9zM3 9h4v4H3zM9 9h4v4H9z',
	search: 'M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10zM14 14l-3.5-3.5',
	plus: 'M8 3v10M3 8h10',
	x: 'M4 4l8 8M12 4l-8 8',
	chev: 'M4 6l4 4 4-4',
	arrow: 'M3 8h10M9 4l4 4-4 4',
	hash: 'M6 2L5 14M11 2l-1 12M2.5 5.5h11M2 10.5h11',
	user: 'M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM2.5 14c.8-2.6 3-4 5.5-4s4.7 1.4 5.5 4',
	check: 'M3 8.5l3 3 7-7',
	logout: 'M6 3H3v10h3M10 5l3 3-3 3M13 8H6',
	undo: 'M5 3L2 6l3 3M2 6h7.5a3.5 3.5 0 0 1 0 7H7',
	lock: 'M4 7h8v6H4zM6 7V5a2 2 0 0 1 4 0v2',
	edit: 'M10.5 2.5l3 3L6 13H3v-3z',
	warn: 'M8 2l6.5 11h-13zM8 6.5v3M8 11.5v.01',
	key: 'M6 10a3 3 0 1 1 2.8-4H14v2h-1.5v1.5H11V8H8.8A3 3 0 0 1 6 10z',
	ext: 'M9 3h4v4M13 3L7 9M11 9.5V13H3V5h3.5',
	run: 'M5 3l8 5-8 5z',
	tickets:
		'M2 4.5h12v2.2a1.6 1.6 0 0 0 0 3.1v2.2H2V9.8a1.6 1.6 0 0 0 0-3.1zM10.5 4.5v7.5'
} as const;

export type IconName = keyof typeof ICONS;
