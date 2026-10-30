import { legal } from '#lib/legal/load.ts';

export const prerender = true;
export const ssr = true;
export const load = () => legal('terms');
