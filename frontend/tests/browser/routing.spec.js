import { test, expect, configure, enterKiosk, openPickup, home, completion, posts,
  kioskB, itemId, regId, storageKey } from './fixture.js';

test('root redirects; web and kiosk support direct entry, reload and idle history navigation', async ({ page, model }) => {
  await page.goto('/');
  await expect(page).toHaveURL(/\/web$/);
  await page.reload();
  await expect(page.getByRole('heading', { name: '어떤 서비스를 이용하시나요?' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: '서비스 이동' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /장치 상태 보기/ })).toHaveCount(0);
  await expect(page.locator('a[href^="/kiosk"]')).toHaveCount(0);
  await page.goto('/kiosk');
  await expect(page).toHaveURL(/\/kiosk$/);
  await expect(page.getByRole('button', { name: /분실물 수령 인증/ })).toBeDisabled();
  await configure(page, kioskB);
  await page.reload();
  await expect(page.getByText('테스트 키오스크 B', { exact: true })).toBeVisible();
  await expect(page.getByLabel('사용할 키오스크 선택')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '키오스크 변경' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '설정 초기화' })).toHaveCount(0);
  await expect(page.locator('a[href^="/web"], a[href^="/admin"], a[href$="/settings"]')).toHaveCount(0);
  await page.goBack();
  await expect(page).toHaveURL(/\/web$/);
  await page.goForward();
  await expect(page).toHaveURL(/\/kiosk$/);
  await expect(page.getByText('테스트 키오스크 B', { exact: true })).toBeVisible();
  expect(posts(model)).toEqual([]);
});

test('input flow blocks repeated Back to settings without losing input', async ({ page, model }) => {
  await enterKiosk(page);
  await page.getByRole('button', { name: /분실물 수령 인증/ }).click();
  await page.getByPlaceholder('OTP 8자리 입력').fill('ABCD2345');
  await page.evaluate(() => history.back());
  await expect(page.getByRole('alertdialog')).toBeVisible();
  await expect(page).toHaveURL(/\/kiosk$/);
  await page.getByRole('button', { name: '현재 업무 계속하기' }).click();
  await page.evaluate(() => history.back());
  await expect(page.getByRole('alertdialog')).toBeVisible();
  await expect(page).toHaveURL(/\/kiosk$/);
  await page.getByRole('button', { name: '현재 업무 계속하기' }).click();
  await expect(page.getByPlaceholder('OTP 8자리 입력')).toHaveValue('ABCD2345');
  await home(page).click();
  await page.goBack();
  await expect(page).toHaveURL(/\/kiosk\/settings$/);
  expect(posts(model)).toEqual([]);
});

test('pending open request blocks navigation; no duplicate request is sent', async ({ page, model }) => {
  let release;
  model.openGate = new Promise((resolve) => { release = resolve; });
  await enterKiosk(page);
  await page.getByRole('button', { name: /분실물 수령 인증/ }).click();
  await page.getByPlaceholder('OTP 8자리 입력').fill('ABCD2345');
  await page.getByRole('button', { name: '인증 승인', exact: true }).click();
  await expect(home(page)).toBeDisabled();
  await page.evaluate(() => history.back());
  await expect(page.getByRole('alertdialog')).toBeVisible();
  release();
  await page.getByRole('button', { name: '현재 업무 계속하기' }).click();
  await expect(completion(page)).toBeEnabled();
  expect(posts(model).map((r) => r.path)).toEqual(['/api/pickup/open']);
});

test('409, Home and resume retain the same pickup and kiosk until successful completion', async ({ page, model }) => {
  model.completeErrors = ['ITEM_STILL_INSIDE'];
  await enterKiosk(page, kioskB);
  await openPickup(page);
  await completion(page).click();
  await expect(page.getByText(/\(ITEM_STILL_INSIDE\)/)).toBeVisible();
  const count = posts(model).length;
  await home(page).click();
  await expect(page.getByRole('button', { name: /분실물 수령 인증/ })).toBeDisabled();
  await expect(page.getByRole('button', { name: '키오스크 변경' })).toHaveCount(0);
  await expect(page.getByText('테스트 키오스크 B', { exact: true })).toBeVisible();
  expect(posts(model)).toHaveLength(count);
  await page.evaluate(() => history.back());
  await page.getByRole('button', { name: '현재 업무 계속하기' }).click();
  await page.getByRole('button', { name: '진행 중인 업무 계속하기' }).click();
  await page.getByRole('button', { name: '보관함 다시 열기' }).click();
  await completion(page).click();
  await expect(page.getByRole('heading', { name: '물건을 찾아가셨습니다' })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/kiosk\/settings$/);
  expect(posts(model).map((r) => r.path)).toEqual(['/api/pickup/open', `/api/pickup/${itemId}/complete`, `/api/pickup/${itemId}/reopen`, `/api/pickup/${itemId}/complete`]);
  expect(posts(model)[0].body.kiosk_id).toBe(kioskB);
});

test('capture failure and Home retain registration; resume does not create a second registration', async ({ page, model }) => {
  model.captureError = true;
  await enterKiosk(page);
  await page.getByRole('button', { name: /분실물 등록 습득/ }).click();
  await page.getByRole('combobox').selectOption('전자기기');
  await page.getByRole('button', { name: /다음: 물건 촬영하기/ }).click();
  await page.getByRole('button', { name: '촬영하기', exact: true }).click();
  await expect(page.getByRole('heading', { name: '요청을 처리하지 못했습니다' })).toBeVisible();
  await page.evaluate(() => history.back());
  await page.getByRole('button', { name: '현재 업무 계속하기' }).click();
  await home(page).click();
  await expect(page.getByRole('button', { name: '키오스크 변경' })).toHaveCount(0);
  await page.getByRole('button', { name: '진행 중인 업무 계속하기' }).click();
  await expect(page.getByRole('heading', { name: '1차 촬영', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '뒤로가기', exact: true }).click();
  await expect(page.getByRole('button', { name: /분실물 등록 습득/ })).toBeDisabled();
  expect(posts(model).map((r) => r.path)).toEqual(['/api/registrations', `/api/registrations/${regId}/shots/1`]);
});

test('uncertain open failure cannot be treated as cancellation by Home', async ({ page, model }) => {
  model.openError = true;
  await enterKiosk(page);
  await page.getByRole('button', { name: /분실물 수령 인증/ }).click();
  await page.getByPlaceholder('OTP 8자리 입력').fill('ABCD2345');
  await page.getByRole('button', { name: '인증 승인', exact: true }).click();
  await expect(page.getByText(/\(DEVICE_UNREACHABLE\)/)).toBeVisible();
  await home(page).click();
  await expect(page.getByText(/관리자에게 서버의 진행 상태 확인/)).toBeVisible();
  await expect(page.getByRole('button', { name: /분실물 수령 인증/ })).toBeDisabled();
  await expect(page.getByRole('button', { name: '키오스크 변경' })).toHaveCount(0);
});

test('reload and document navigation show beforeunload; accepting reload does not repeat business requests', async ({ page, model }) => {
  await enterKiosk(page, kioskB);
  await openPickup(page);
  let dialogs = 0;
  const dismiss = async (dialog) => { dialogs++; expect(dialog.type()).toBe('beforeunload'); await dialog.dismiss(); };
  page.on('dialog', dismiss);
  await page.evaluate(() => { setTimeout(() => window.location.reload(), 0); });
  await expect.poll(() => dialogs).toBe(1);
  await expect(completion(page)).toBeVisible();
  await page.evaluate(() => { window.location.href = 'http://localhost:5189/web'; });
  await expect.poll(() => dialogs).toBe(2);
  await expect(page).toHaveURL(/\/kiosk$/);
  page.off('dialog', dismiss);
  page.once('dialog', (dialog) => dialog.accept());
  await page.reload();
  await expect(page.getByText('테스트 키오스크 B', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: '무엇을 도와드릴까요?' })).toBeVisible();
  expect(posts(model).map((r) => r.path)).toEqual(['/api/pickup/open']);
});

test('developer OTP stays local and completion releases the navigation guard', async ({ page, model }) => {
  await enterKiosk(page);
  await openPickup(page, 'DEV00000');
  await home(page).click();
  await page.getByRole('button', { name: '진행 중인 업무 계속하기' }).click();
  await completion(page).click();
  await page.goBack();
  await expect(page).toHaveURL(/\/kiosk\/settings$/);
  expect(posts(model)).toEqual([]);
});

test('another tab cannot change the kiosk during a workflow; idle revalidates it', async ({ page, context, model }) => {
  await enterKiosk(page);
  await page.getByRole('button', { name: /분실물 수령 인증/ }).click();
  const other = await context.newPage();
  await other.goto('/web');
  await other.evaluate(({ key, value }) => localStorage.setItem(key, value), { key: storageKey, value: kioskB });
  await expect(page.getByText('테스트 키오스크 A', { exact: true })).toBeVisible();
  await home(page).click();
  await expect(page.getByText('테스트 키오스크 B', { exact: true })).toBeVisible();
  await other.close();
  expect(posts(model)).toEqual([]);
});
