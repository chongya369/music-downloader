// 登录设备列表

const createOption = require('../util/option.js')
module.exports = (query, request) => {
  const data = {
    excStatus: '9',
  }
  return request(
    `/api/middle/user/device/list`,
    data,
    createOption(query, 'eapi'),
  )
}
