import { chromium, expect } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import assert from 'node:assert/strict';
import { createPreviewServer } from '../preview.mjs';
const server = await createPreviewServer(0);
const base = `http://127.0.0.1:${server.address().port}`;
const executablePath = process.env.CHROMIUM_PATH || (existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined);
const browser = await chromium.launch({ executablePath, headless: true, args: ['--no-sandbox'] }).catch(error => { server.close(); throw error; });
const page = await browser.newPage({ viewport: { width: 440, height: 900 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
await mkdir('web/test-results', { recursive: true });
let frame;
async function open(query = '') { await page.goto(base + query); frame = page.frameLocator('iframe'); await expect(frame.locator('#refresh')).toBeEnabled(); }
async function pass(name, fn) { await fn(); console.log(`PASS ${name}`); }
try {
  await pass('initial result renders without redundant list call; scheduled badge is distinct', async () => {
    await open();
    await expect(frame.locator('.post-row')).toHaveCount(5);
    await expect(frame.locator('#count')).toHaveText('5 posts in your library');
    await expect(frame.getByText('Scheduled', { exact: true })).toBeVisible();
    assert.deepEqual(await page.evaluate(() => window.demo.calls), []);
    await page.screenshot({ path: 'web/test-results/library-light.png', fullPage: true });
  });
  await pass('search, filters and reset use expected server contract', async () => {
    await frame.locator('#search').fill('slower');
    await expect(frame.locator('.post-row')).toHaveCount(1);
    await frame.getByRole('button', { name: 'Drafts', exact: true }).click();
    await expect(frame.locator('#count')).toHaveText('1 post found');
    assert.deepEqual((await page.evaluate(() => window.demo.calls)).at(-1), { name: 'list_posts', args: { query: 'slower', status: 'draft', limit: 50, offset: 0 } });
    await frame.getByRole('button', { name: 'Refresh posts' }).click();
    await expect(frame.locator('.post-row')).toHaveCount(5);
    assert.deepEqual((await page.evaluate(() => window.demo.calls)).at(-1), { name: 'list_posts', args: { query: '', status: 'all', limit: 50, offset: 0 } });
  });
  await pass('post reader, public link and back navigation', async () => {
    await frame.getByRole('button', { name: 'The small things we choose to keep, Published' }).click();
    await expect(frame.locator('#post-body')).toContainText('## Making room');
    await expect(frame.locator('#reader-content')).toHaveAttribute('aria-busy', 'false');
    await frame.getByRole('button', { name: 'View on blog' }).click();
    await expect.poll(() => page.evaluate(() => window.demo.links)).toEqual(['https://example.mataroa.blog/blog/small-things/']);
    await page.screenshot({ path: 'web/test-results/post-reader.png', fullPage: true });
    await frame.getByRole('button', { name: 'All posts', exact: true }).click();
    await expect(frame.locator('#library')).toBeVisible();
    await frame.getByRole('button', { name: 'A slower kind of internet, Draft' }).click();
    await expect(frame.locator('#post-body')).toContainText('unfinished thought');
    await expect(frame.locator('#open-post')).toBeHidden();
    await frame.getByRole('button', { name: 'All posts', exact: true }).click();
  });
  await pass('late post responses cannot reopen dismissed content', async () => {
    await page.evaluate(() => { window.demo.holdPosts = true; });
    await frame.locator('.post-row').first().click();
    await expect(frame.locator('#post-body')).toHaveText('Loading post…');
    await frame.getByRole('button', { name: 'All posts', exact: true }).click();
    await page.evaluate(() => window.demo.releasePosts());
    await expect(frame.locator('#reader')).toBeHidden();
    await expect(frame.locator('#library')).toBeVisible();
  });
  await pass('latest query wins over slow earlier results', async () => {
    await page.evaluate(() => { window.demo.delays.slow = 700; });
    await frame.locator('#search').fill('slow');
    await frame.locator('#search').press('Enter');
    await frame.locator('#search').fill('autumn');
    await frame.locator('#search').press('Enter');
    await expect(frame.locator('.post-row')).toHaveCount(1);
    await expect(frame.locator('.row-title')).toHaveText('Letters from the edge of autumn');
    await page.waitForTimeout(850);
    await expect(frame.locator('.row-title')).toHaveText('Letters from the edge of autumn');
  });
  await pass('load failures show retry and recover', async () => {
    await page.evaluate(() => { window.demo.failNext = 'list_posts'; });
    await frame.getByRole('button', { name: 'Refresh posts' }).click();
    await expect(frame.locator('#library-notice')).toBeVisible();
    await frame.getByRole('button', { name: 'Try again', exact: true }).click();
    await expect(frame.locator('.post-row')).toHaveCount(5);
    await page.evaluate(() => { window.demo.failNext = 'get_post'; });
    await frame.locator('.post-row').first().click();
    await expect(frame.locator('#reader-notice')).toBeVisible();
    await frame.getByRole('button', { name: 'Try again', exact: true }).click();
    await expect(frame.locator('#post-body')).toContainText('Making room');
  });
  await pass('host theme changes apply and narrow view has no horizontal overflow', async () => {
    await open();
    await page.evaluate(() => window.demo.theme('dark'));
    await expect(frame.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.screenshot({ path: 'web/test-results/library-dark.png', fullPage: true });
    await page.setViewportSize({ width: 280, height: 780 });
    assert.equal(await frame.locator('body').evaluate(node => node.scrollWidth > window.innerWidth), false);
    await page.screenshot({ path: 'web/test-results/library-narrow.png', fullPage: true });
    await page.setViewportSize({ width: 440, height: 900 });
  });
  await pass('untrusted title, excerpt and body are inert text and unsafe URLs stay hidden', async () => {
    await open('?mode=injection');
    await frame.locator('.post-row').first().click();
    await expect(frame.locator('#post-body')).toContainText('<script>window.hacked = true</script>');
    await expect(frame.locator('#open-post')).toBeHidden();
    assert.equal(await frame.locator('body').evaluate(() => window.hacked), undefined);
    assert.equal(await frame.locator('#reader img, #reader script').count(), 0);
  });
  await pass('empty library and no-match states are clear', async () => {
    await open('?mode=empty');
    await expect(frame.getByText('A little room for words')).toBeVisible();
    await open();
    await frame.locator('#search').fill('no-such-post');
    await expect(frame.getByText('No matching words, yet')).toBeVisible();
    await frame.getByRole('button', { name: 'Show all posts' }).click();
    await expect(frame.locator('.post-row')).toHaveCount(5);
  });
  await pass('initial error can recover without reconnecting', async () => {
    await open('?mode=error');
    await expect(frame.locator('#library-notice')).toBeVisible();
    await frame.getByRole('button', { name: 'Refresh posts' }).click();
    await expect(frame.locator('.post-row')).toHaveCount(5);
  });
  await pass('pagination appends without duplication and uses server offset', async () => {
    await open('?mode=pagination');
    await expect(frame.locator('.post-row')).toHaveCount(50);
    await frame.getByRole('button', { name: 'Load more' }).click();
    await expect(frame.locator('.post-row')).toHaveCount(59);
    await expect(frame.locator('#load-more')).toBeHidden();
    assert.equal((await page.evaluate(() => window.demo.calls)).at(-1).args.offset, 50);
  });
  assert.deepEqual(errors, [], 'No uncaught browser exceptions');
  console.log('All browser checks passed. Screenshots: web/test-results/');
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
