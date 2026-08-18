const { resourceTypeMap } = require('../util/config.json')
// 删除评论

const createOption = require('../util/option.js')
module.exports = (query, request) => {
  const data = {
    commentId: query.cid,
    threadId: resourceTypeMap[query.type] + query.id,
  }
  return request(
    `/api/resource/comments/delete`,
    data,
    createOption(query, 'xeapi'),
  )
}
