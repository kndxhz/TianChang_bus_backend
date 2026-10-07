## 天长公交查询 API

这是一个仅提供 `GET` 查询的 Flask API。服务端访问旧版公交数据源时会自动生成签名，并自动执行旧 APP 的 `EncryptCodeString` 字符转换和 URL 编码，客户端不需要提供 `apikey`、`timeStamp`、`Random` 或 `SignKey`。

### 启动

```bash
cp .env.example .env
uv sync
uv run tianchang-bus-backend
```

默认监听 `http://127.0.0.1:5000`。可通过 `HOST`、`PORT`、`TIANCHANG_UPSTREAM_URL`、`TIANCHANG_APP_KEY` 和 `TIANCHANG_REQUEST_TIMEOUT` 配置。

根密钥从本地 `.env` 加载，`.env` 不会提交到 Git；部署时请通过环境变量或部署平台的 Secret 配置提供 `TIANCHANG_APP_KEY`。

### 查询接口

| 接口 | 参数 | 用途 |
| --- | --- | --- |
| `GET /health` | 无 | 健康检查 |
| `GET /api/routes` | 无 | 可直接查询的全部子线路 |
| `GET /api/routes/search?name=101` | `name` | 按线路名查找可查询子线路 |
| `GET /api/routes/{route_id}/buses?segment_id=...` | `segment_id` | 线路运行车辆 |
| `GET /api/routes/{route_id}/schedule` | 无 | 线路班次计划 |
| `GET /api/routes/{route_id}/moments` | 无 | 线路时刻信息 |
| `GET /api/routes/{route_id}/stations` | 无 | 线路站点 |
| `GET /api/routes/{route_id}/line` | 无 | 线路地图/折线 |
| `GET /api/routes/{route_id}/directions` | 无 | 查询线路方向和站点 |
| `GET /api/realtime?route=101&station=十八集大道&direction=到客运中心方向` | `route`、`station`、可选 `direction` | 按线路、站点和模糊目的地方向查询实时公交 |
| `GET /api/stations/{station_id}/realtime?route_id=...` | `route_id`, 可选 `combine` | 站点实时车辆 |
| `GET /api/stations/nearby?latitude=...&longitude=...&range=2000` | 经纬度，可选范围 | 附近站点 |
| `GET /api/stations/search?name=...` | `name` | 按名称搜索站点 |
| `GET /api/buses/search?name=...` | `name` | 模糊搜索公交 |

所有业务接口只接受 `GET`。上游不可用时返回 `502`，参数缺失时返回 `400`。

推荐使用 `/api/realtime` 按线路名、站点名和模糊目的地方向查询。`direction` 支持 `客运中心`、`到客运中心方向`、`往汊涧镇` 等写法，并按终点名称包含关系匹配。如果匹配到多个方向，接口返回 `409` 和候选方向。底层接口仍可使用 `/api/routes` 返回的 `RouteID`，例如 101 路子线路 ID 为 `210101`。
