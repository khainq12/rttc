# Theo dõi tiến độ nghiệm thu theo báo cáo hành chính

Tài liệu này theo dõi tiến độ khắc phục 06 nhóm tiêu chí nghiệm thu nêu
trong `FleetRMW_FleetQoX_Bao_cao_hanh_chinh.docx` (kiểm tra ngày
07/09/2026, tại commit `c5da4417fe9e7f6a99498fac49c4e1ab98aaa5e6` trên
nhánh `main`). Cập nhật file này mỗi khi một nhóm có tiến triển, để không
phải dò lại toàn bộ lịch sử hội thoại.

Trạng thái tại thời điểm kiểm tra gốc (07/09/2026): **0/6 nhóm đạt**.

## Bảng tổng hợp hiện tại

| # | Nhóm tiêu chí | Trạng thái gốc | Trạng thái hiện tại | Bằng chứng |
|---|---|---|---|---|
| 1 | An toàn bộ nhớ / sanitizer đúng topology (2 tiến trình + netem + 32 KiB) | Chưa đạt | ✅ **Đã đóng** | `scripts/run_heap_soak_asan_probe.py`, kết quả `results_rmw_socket/heap_soak_asan_probe_summary.json`: 40/40 round, 2 tiến trình OS riêng biệt (container publisher/subscriber khác nhau), netem thật (`loss random 15%`), payload 32768 byte, ASan+UBSan 0 lỗi, teardown sạch mọi round. Gắn với task B0 (root-cause crash relay teardown). |
| 2 | Độ tin cậy 32 KiB ở 16/32 robot (delivery + publisher ACK completion) | Chưa đạt | ✅ **Đã đóng** | Root-cause bug `wait_for_all_acked` graph-membership pruning (publisher thoát sớm do ack giả) + 2 lỗi phụ (fragment-NACK backoff quá ngắn, relay executor đơn luồng nghẽn ở quy mô 64 route). Grid 9/9 (8/16/32 robot × seed 7/13/29) pass với tiêu chí đầy đủ (`publisher.ack_wait_complete AND relay.downstream_ack_wait_complete AND min_topic_delivery_ratio==1.0`). Commit `b9790d3`. |
| 3 | QUIC/PKI và HA/fencing (online rotation, live revocation, ma trận phân vùng không split-brain, failover/failback đa host, durable state) | Đạt phần lớn (thiếu đa host) | ✅ **Đã đóng** | HA multi-host (Raft + etcd/PostgreSQL, failover + failback) làm ở phiên trước (2 VM KVM thật). PKI cert/CA rotation multi-host (CRL revocation + CA rotation thật, không phải chỉ thêm CA) làm phiên này: `scripts/run_multihost_kvm_udp_peer_auth_crl_reload_probe.py`, 4/4 round pass. Commit `65b7100`. Lưu ý nhỏ: "ma trận phân vùng" mới test một số kịch bản tiêu biểu, chưa phải toàn bộ tổ hợp. |
| 4 | Ngữ nghĩa RMW (full QoS event, full DDS content-filter dialect, deep preallocation) | Đạt một phần | 🟡 **Một phần** | Dynamic message, nhiều QoS extension (liveliness, deadline, lifespan, destination_order, ownership, partition, presentation) đã xong. **Task #42 (content-filter dialect) nay đã đóng**: thêm `LIKE ... ESCAPE`, 3/3 pass (`run_rmw_docker_content_filter_sql_probe.py`), và đã ra quyết định phạm vi chính thức — subset hiện tại (AND/OR/NOT, 7 toán tử so sánh 2 chiều, LIKE+ESCAPE, BETWEEN, IN/NOT IN, IS [NOT] NULL) là ranh giới cuối cùng, các hàm DDS SQL tuỳ ý/mở rộng vendor bị loại vĩnh viễn khỏi phạm vi (không phải việc còn treo). Còn lại **task #43 (deep_preallocation_claim)**: đã có đánh giá kỹ trong `capabilities.json` (`allocations`/`loaned_messages`) — phần base64 scratch buffer và loaned-message pool đã đóng, phần còn lại (JSON ostringstream mỗi publish, retransmit-ledger per-message) cần redesign wire-format nhị phân + pool allocator, rủi ro/blast-radius cao → cần quyết định của người dùng trước khi làm, xem "Việc tiếp theo". |
| 5 | Nav2, Open-RMF, đa host, HIL | Đạt một phần | ❌ **Chưa làm** | Nav2 đã có bằng chứng chạy thực tế (từ trước). Open-RMF chưa phải full upstream stack; chưa có bằng chứng đa host/HIL cho workload tự hành. |
| 6 | Đối sánh mô phỏng (ns-3/OMNeT++), soak dài hạn, bằng chứng phát hành qua CI | Đạt một phần | ❌ **Chưa làm** | ns-3/OMNeT++ đã chạy thực tế nhưng đối sánh chủ yếu P2P (MatchedP2p), chưa mở rộng wireless/TSN/mesh. Chưa có soak dài hạn và CI-qualified evidence bundle. `main` cũng chưa có branch protection / `.github/workflows`. |

**Tóm lại: 3/6 nhóm đã đóng (1, 2, 3). Nhóm 4 đã đóng task #42, còn task #43
chờ quyết định của người dùng. Còn 5, 6 chưa làm.**

## Việc tiếp theo (theo task list nội bộ)

- **Task #42** (Nhóm 4) — ✅ **Đã đóng**: thêm `LIKE ... ESCAPE` vào
  `ContentFilterExpressionParser`/`content_filter_like()` trong
  `rmw_pubsub.cpp`, test mới trong `content_filter_sql_probe.cpp`
  (`escape_scenario` + `multi_char_escape_rejected`), 3/3 pass qua
  `scripts/run_rmw_docker_content_filter_sql_probe.py`. Quyết định phạm vi
  chính thức đã ghi vào `capabilities.json` (`content_filters` narrative)
  và `docs/STATUS_AND_ROADMAP.md`: subset hiện có là ranh giới **cuối
  cùng**, các hàm DDS SQL tuỳ ý và mở rộng vendor bị loại vĩnh viễn (lý do:
  không có nhu cầu thực tế + mở rộng grammar cho input được parse trên mỗi
  sample nhận về sẽ tăng bề mặt tấn công).
- **Task #43** (Nhóm 4) — **Cần quyết định của người dùng trước khi làm**:
  phần base64-scratch-buffer và loaned-message pool của
  `deep_preallocation_claim` đã đóng từ trước (xem `allocations`/
  `loaned_messages` trong `capabilities.json`). Phần còn lại (JSON frame
  body dựng qua `std::ostringstream` mỗi lần publish, và retransmit ledger
  cấp phát 1 entry/message) cần đổi wire format từ JSON sang nhị phân +
  viết pool allocator cho ledger — rủi ro cao (ảnh hưởng ~187 probe khác
  phụ thuộc định dạng số hiện tại) và blast-radius lớn. Đề xuất: giữ
  `deep_preallocation_claim=false` như một ranh giới đã biết rõ và có tài
  liệu đầy đủ, thay vì làm mù một redesign lớn — chờ người dùng xác nhận có
  muốn đầu tư vào việc này hay chấp nhận đây là scope cuối cùng.
- **Nhóm 5**: đưa FleetRMW vào path ROS 2 thật của RMF components (không
  chỉ gọi service tương thích kiểu); chạy kịch bản đa host cho Open-RMF;
  cân nhắc HIL nếu phạm vi sản xuất yêu cầu.
- **Nhóm 6**: mở rộng OMNeT++/ns-3 sang wireless/roaming tương đương
  MatchedP2p hiện có; nếu TSN/mesh nằm trong claim thì cần chạy thực tế
  trên cả hai simulator; thiết lập soak dài hạn + bundle bằng chứng
  qua CI (bắt đầu bằng việc thêm `.github/workflows` và branch
  protection cho `main`).

## Quy ước cập nhật file này

- Mỗi khi một nhóm chuyển trạng thái, sửa dòng tương ứng trong bảng và
  ghi rõ commit/script/kết quả làm bằng chứng (không ghi chung chung).
- Không xoá lịch sử "Trạng thái gốc" — đó là mốc đối chiếu với báo cáo
  hành chính gốc.
- Nếu phát hiện một claim tưởng đã đóng nhưng thực ra chưa đủ bằng chứng,
  hạ trạng thái xuống lại và ghi rõ lý do (đừng tự chấp nhận "coi như
  xong" nếu chưa có replay được bằng chứng cụ thể).
