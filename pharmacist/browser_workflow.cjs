/* 可选浏览器验收：先启动 python -m pharmacist.e2e_server。
 * 使用预先安装的 Playwright 与本机 Edge；不安装任何依赖。
 * 只允许连接隔离服务固定端口，所有业务通过网页点击，绝不注入 Session/模拟响应。
 */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const base = 'http://127.0.0.1:8767';
const out = fs.mkdtempSync(path.join(os.tmpdir(), 'pharmacist-workflow-browser-'));
const edgePath = process.env.EDGE_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const phones = { patient: '13990000001', doctor: '13990000002', pharmacist: '13990000003' };
// 仅本轮合成测试账号的演示口令，与 test_support.py 一致。
const demoPassword = 'LocalWorkflowDemo2026!';

(async () => {
  const browser = await chromium.launch({ executablePath: edgePath, headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  async function login(role) {
    await page.goto(base + '/accounts/logout/');
    await page.goto(base + (role === 'patient' ? '/accounts/login/' : '/accounts/staff/' + role + '/'));
    await page.locator('[name="phone"]').fill(phones[role]);
    await page.locator(role === 'patient' ? '[name="password"]' : '[name="secret"]').fill(demoPassword);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(base + (role === 'patient' ? '/accounts/' : '/' + role + '/'));
  }
  async function openPatientPrescription(id) {
    await page.getByRole('link', { name: '我的处方', exact: true }).click();
    await page.locator('a[href="/accounts/prescriptions/' + id + '/"]').click();
  }
  async function openPharmacistPrescription(id, queue) {
    await page.locator('.side a[href="/pharmacist/prescriptions/' + queue + '/"]').click();
    await page.getByRole('link', { name: '查看处方 ' + id + ' 详情', exact: true }).click();
  }
  async function submitDraft() {
    const button = page.getByRole('button', { name: '提交药师审核', exact: true });
    await button.click();
    await page.waitForURL('**/doctor/prescriptions/');
  }
  async function createPrescription() {
    await login('patient');
    await page.getByRole('link', { name: '线上问诊', exact: true }).click();
    await page.locator('select[name="dept"]').selectOption({ label: '测试内科' });
    await page.locator('input[name="doctor"]').check();
    await page.getByRole('button', { name: '提交挂号', exact: true }).click();
    await page.waitForURL('**/assess/');
    const cid = Number(page.url().match(/consult\/(\d+)/)[1]);
    await page.locator('textarea[name="answer"]').fill('轻微咳嗽两天，无其他症状');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await page.getByRole('button', { name: '病情已补充完，生成评估', exact: true }).click();
    await page.waitForURL(base + '/accounts/consult/' + cid + '/');
    await login('doctor');
    await page.locator('a[href="/doctor/consult/' + cid + '/"]').first().click();
    await page.locator('input[name="result"][value="采纳"]').check();
    await page.getByRole('button', { name: '提交复核', exact: true }).click();
    await page.getByRole('link', { name: '进入图文沟通', exact: true }).click();
    await page.getByRole('link', { name: '结束问诊并给出处置建议', exact: true }).click();
    await page.locator('textarea[name="final_diagnosis"]').fill('浏览器端到端测试诊断');
    await page.locator('input[name="advice"][value="need_drug"]').check();
    await page.getByRole('button', { name: '提交处置建议', exact: true }).click();
    const firstDrug = page.locator('select[name="drug_id"]');
    await firstDrug.selectOption(await firstDrug.locator('option').filter({ hasText: '联调药品甲' }).getAttribute('value'));
    await page.locator('input[name="qty"]').fill('2');
    await page.locator('input[name="dosage"]').fill('测试医嘱甲');
    await page.getByRole('button', { name: '＋ 添加一行', exact: true }).click();
    const secondDrug = page.locator('select[name="drug_id"]').nth(1);
    await secondDrug.selectOption(await secondDrug.locator('option').filter({ hasText: '联调药品乙' }).getAttribute('value'));
    await page.locator('input[name="qty"]').nth(1).fill('3');
    await page.locator('input[name="dosage"]').nth(1).fill('测试医嘱乙');
    await page.getByRole('button', { name: '保存草稿', exact: true }).click();
    await page.waitForURL('**/review/');
    const id = Number(page.url().match(/prescriptions\/(\d+)/)[1]);
    await submitDraft();
    assert((await page.locator('body').innerText()).includes('待审核'));
    return id;
  }
  try {
    const normalId = await createPrescription();
    await login('pharmacist');
    await page.locator('.pharmacist-stat').first().click();
    await page.getByRole('link', { name: '查看处方 ' + normalId + ' 详情', exact: true }).click();
    assert((await page.locator('body').innerText()).includes('测试医嘱乙'));
    await page.screenshot({ path: path.join(out, 'audit-before.png'), fullPage: true });
    await page.getByRole('button', { name: '审核通过', exact: true }).click();
    assert((await page.locator('.pharmacist-detail-head').innerText()).includes('已通过'));
    await login('patient');
    await openPatientPrescription(normalId);
    await page.locator('[name="pickup_method"][value="0"]').check();
    await page.locator('select[name="item_decoction"]').first().selectOption('1');
    await page.locator('[name="item_decoction_num"]').first().fill('2');
    await page.getByRole('button', { name: '确认取药方式与代煎', exact: true }).click();
    assert((await page.locator('body').innerText()).includes('待配药'));
    await login('pharmacist');
    await openPharmacistPrescription(normalId, 'pending-dispense');
    await page.screenshot({ path: path.join(out, 'dispense-before.png'), fullPage: true });
    await page.locator('[name="checked"]').check();
    await page.locator('[name="decoction_done"]').check();
    await page.getByRole('button', { name: '确认实际发药', exact: true }).click();
    assert((await page.locator('.pharmacist-detail-head').innerText()).includes('已完成'));
    await login('patient');
    await openPatientPrescription(normalId);
    const patientText = await page.locator('body').innerText();
    assert(patientText.includes('已完成') && patientText.includes('已取药'));
    await page.screenshot({ path: path.join(out, 'patient-completed.png'), fullPage: true });

    const rejectedId = await createPrescription();
    await login('pharmacist');
    await openPharmacistPrescription(rejectedId, 'pending-review');
    await page.locator('[name="reason"]').fill('浏览器联调：请修改第二项用法后重新提交');
    await page.getByRole('button', { name: '驳回处方', exact: true }).click();
    assert((await page.locator('.pharmacist-detail-head').innerText()).includes('已驳回'));
    await login('doctor');
    await page.getByRole('link', { name: '我的处方', exact: true }).click();
    assert((await page.locator('body').innerText()).includes('请修改第二项用法'));
    await page.locator('a[href="/doctor/prescriptions/' + rejectedId + '/edit/"]').click();
    await page.locator('input[name="dosage"]').nth(1).fill('修订后的测试用法乙');
    await page.getByRole('button', { name: '保存草稿', exact: true }).click();
    await submitDraft();
    await login('pharmacist');
    await openPharmacistPrescription(rejectedId, 'pending-review');
    assert((await page.locator('body').innerText()).includes('修订后的测试用法乙'));
    await page.getByRole('button', { name: '审核通过', exact: true }).click();
    await login('patient');
    await openPatientPrescription(rejectedId);
    await page.locator('[name="pickup_method"][value="1"]').check();
    await page.getByRole('button', { name: '确认取药方式与代煎', exact: true }).click();
    assert((await page.locator('body').innerText()).includes('电子处方外购凭证'));
    await page.screenshot({ path: path.join(out, 'external-voucher.png'), fullPage: true });

    await login('pharmacist');
    await page.locator('.side a[href="/pharmacist/prescriptions/processed/"]').click();
    await page.locator('select[name="status"]').selectOption('已通过');
    await page.locator('select[name="sort"]').selectOption('oldest');
    await page.getByRole('button', { name: '应用筛选', exact: true }).click();
    assert(page.url().includes('sort=oldest'));
    await page.setViewportSize({ width: 375, height: 812 });
    await page.screenshot({ path: path.join(out, 'processed-mobile.png'), fullPage: true });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.getByRole('link', { name: '恢复默认', exact: true }).click();
    assert(!page.url().includes('?'));
    await page.getByRole('link', { name: '查看处方 ' + normalId + ' 详情', exact: true }).click();
    await page.screenshot({ path: path.join(out, 'completed-mobile.png'), fullPage: true });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    assert.deepEqual(errors, []);
    const result = { passed: true, normalId, rejectedId, artifacts: out,
                     verified: ['real login', 'two-item prescription', 'audit', 'pickup/decoction',
                                'dispense', 'patient completion', 'reject/edit/resubmit',
                                'external voucher', 'filters', '375px layout', 'no JS errors'] };
    fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    await page.screenshot({ path: path.join(out, 'failure.png'), fullPage: true });
    console.error('FAILED at', page.url(), error.message, 'artifacts:', out);
    process.exitCode = 1;
  } finally { await browser.close(); }
})();
