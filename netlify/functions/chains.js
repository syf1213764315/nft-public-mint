const { netlifyHandler } = require("../../dist/http-api");
exports.handler = netlifyHandler("/api/chains");
