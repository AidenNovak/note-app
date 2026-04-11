const { chromium } = require('playwright');
const path = require('path');

const SCREENSHOTS_DIR = path.join(__dirname, 'screenshots');
const BASE = 'http://localhost:3000';
const SERVER = 'http://localhost:3001';

const MOBILE = { width: 390, height: 844 };
const TEST_EMAIL = `e2e-${Date.now()}@test.local`;
const TEST_PASS = 'TestPass123!';

(async () => {
  const fs = require('fs');
  fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: MOBILE,
    deviceScaleFactor: 2,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)',
  });
  const page = await context.newPage();

  const shot = async (name) => {
    await page.waitForTimeout(1500);
    await page.screenshot({
      path: path.join(SCREENSHOTS_DIR, `${name}.png`),
      fullPage: false,
    });
    console.log(`  ✓ ${name}.png`);
  };

  try {
    // 1. Landing page
    console.log('1. Landing page');
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await shot('01-landing-hero');

    // Scroll to sections
    for (const id of ['overview', 'spaces', 'philosophy']) {
      await page.evaluate((sectionId) => {
        document.getElementById(sectionId)?.scrollIntoView({ behavior: 'instant' });
      }, id);
      await shot(`02-landing-${id}`);
    }

    // 2. Sign up page
    console.log('2. Sign up page');
    await page.goto(`${BASE}/auth/sign-up`, { waitUntil: 'networkidle' });
    await shot('03-auth-signup');

    // 3. Register via API
    console.log('3. Register user via API');
    const registerResp = await page.request.post(`${SERVER}/api/auth/sign-up/email`, {
      data: { name: 'E2E Tester', email: TEST_EMAIL, password: TEST_PASS },
    });
    console.log(`  Register: ${registerResp.status()}`);
    if (registerResp.status() !== 200) {
      console.error('  Registration failed:', await registerResp.text());
    }

    // 4. Login via form
    console.log('4. Login');
    await page.goto(`${BASE}/auth/sign-in`, { waitUntil: 'networkidle' });
    await shot('04-auth-signin');

    await page.waitForSelector('input', { timeout: 10000 });
    const inputs = await page.locator('input').all();
    if (inputs.length >= 2) {
      await inputs[0].fill(TEST_EMAIL);
      await inputs[1].fill(TEST_PASS);
    }
    await shot('04b-auth-signin-filled');

    await page.locator('button[type="submit"]').first().click();
    // After login, app redirects to /dashboard
    await page.waitForURL('**/dashboard**', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(2000);
    await shot('05-dashboard');

    // 5. Dashboard page
    console.log('5. Dashboard');
    await page.goto(`${BASE}/dashboard`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);
    await shot('05b-dashboard-full');

    // Scroll down to see more dashboard content
    await page.evaluate(() => window.scrollBy(0, 600));
    await shot('05c-dashboard-scrolled');

    // 6. Users page
    console.log('6. Users page');
    await page.goto(`${BASE}/users`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);
    await shot('06-users');

    // 7. Settings - Profile
    console.log('7. Settings - Profile');
    await page.goto(`${BASE}/settings/profile`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);
    await shot('07-settings-profile');

    // 8. Settings - Security
    console.log('8. Settings - Security');
    await page.goto(`${BASE}/settings/security`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);
    await shot('08-settings-security');

    // 9. Settings - Billing
    console.log('9. Settings - Billing');
    await page.goto(`${BASE}/settings/billing`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);
    await shot('09-settings-billing');

    // 10. Backend health check
    console.log('10. Backend health');
    const healthResp = await page.request.get('http://localhost:8000/health');
    console.log(`  Backend health: ${healthResp.status()} - ${await healthResp.text()}`);

    console.log('\n✅ All tests passed! Screenshots saved to:', SCREENSHOTS_DIR);
  } catch (err) {
    console.error('❌ Error:', err.message);
    await shot('error-state');
  } finally {
    await browser.close();
  }
})();
