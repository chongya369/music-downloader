// 获取免费听时长状态

const createOption = require('../util/option.js')
module.exports = (query, request) => {
  const data = {
    entrance: 'FREE_LISTEN_RN',
  }
  return request(
    `/api/ad/homepage/free/tab/extend/v2`,
    data,
    createOption(query, 'xeapi'),
  )
}
