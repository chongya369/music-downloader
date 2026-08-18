// 黑胶乐签打卡

const createOption = require('../util/option.js')

module.exports = async (query, request) => {
  const results = {}

  const taskSign = await request(
    '/api/vip-center-bff/task/sign',
    {},
    createOption(query, 'weapi'),
  )
  results.taskSign = taskSign.body

  const checkinDetail = await request(
    '/api/vipnewcenter/app/level/user/checkin/history/detail',
    {
      signDayTime: Date.now(),
      type: 1,
    },
    createOption(query, 'eapi'),
  )
  results.checkinDetail = checkinDetail.body

  // 两个接口都返回 code=200 即视为打卡成功
  const signed =
    Number(results.taskSign?.code) === 200 &&
    Number(results.checkinDetail?.code) === 200

  return {
    body: {
      code: 200,
      ...results,
      signed,
      message: signed ? '黑胶乐签打卡成功' : '黑胶乐签打卡失败',
    },
    cookie: taskSign.cookie,
    status: 200,
  }
}
