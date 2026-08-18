// 强制下线设备

const createOption = require('../util/option.js')
module.exports = (query, request) => {
  const data = {
    key: query.deviceKey,
    captcha: query.captcha || '',
  }
  return request(
    `/api/middle/user/security/device/kickoff`,
    data,
    createOption(query, 'eapi'),
  )
}
