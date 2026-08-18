// 发送安全验证码

const createOption = require('../util/option.js')
module.exports = (query, request) => {
  const data = {
    ctcode: query.ctcode || '86',
  }
  return request(
    `/api/sms/captcha/safe/sent`,
    data,
    createOption(query, 'eapi'),
  )
}
