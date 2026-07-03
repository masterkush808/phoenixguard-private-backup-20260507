//+------------------------------------------------------------------+
//| PhoenixGuard MT4 Executioner                                     |
//| Consumes PhoenixGuard PG_EXECUTION_PACKET_V3 packets only.       |
//+------------------------------------------------------------------+
#property strict
#property copyright "PhoenixGuard"
#property link      ""
#property version   "808"
#property description "Executes only validated PhoenixGuard PG_EXECUTION_PACKET_V3 BUY/SELL packets with MT4-side risk controls."

enum ENUM_PG_SIGNAL_SOURCE
{
   PG_SIGNAL_WEBREQUEST  = 0,
   PG_SIGNAL_COMMON_FILE = 1
};

enum ENUM_PG_RISK_MODE
{
   PG_RISK_FIXED_LOT         = 0,
   PG_RISK_EQUITY_PERCENT    = 1,
   PG_RISK_BALANCE_PERCENT   = 2,
   PG_RISK_ADAPTIVE_COMPOUND = 3
};

enum ENUM_PG_STOP_MODE
{
   PG_STOP_FIXED_PIPS = 0,
   PG_STOP_ATR        = 1
};

input string                InpProfileName                         = "PhoenixGuard FINAL_LIVE MT4";
input bool                  InpAllowLiveExecution                  = false;
input bool                  InpDryRun                              = true;
input int                   InpMagicNumber                         = 8082026;
input ENUM_PG_SIGNAL_SOURCE InpSignalSource                        = PG_SIGNAL_COMMON_FILE;
input string                InpBaseUrl                             = "http://127.0.0.1:8793";
input string                InpSessionId                           = "pocket-live-8788";
input string                InpEndpointOverride                    = "";
input int                   InpWebTimeoutMs                        = 350;
input string                InpCommonSignalFile                    = "PhoenixGuard\\mt4_execution_command.json";
input int                   InpCommonFileOpenRetries               = 4;
input int                   InpCommonFileRetryDelayMs              = 25;
input int                   InpPollMilliseconds                    = 200;
input int                   InpPacketMaxAgeMs                      = 2500;
input int                   InpPacketExpiryGraceMs                 = 250;
input int                   InpClockSkewToleranceSec               = 3;

input string                InpTradeSymbol                         = "";
input string                InpTrackerSymbol                       = "";
input bool                  InpRequirePacketSymbolMatch            = false;
input string                InpExpectedTimeframe                   = "";
input bool                  InpRequireTradePermission              = true;
input bool                  InpRequireCompleteSequence             = true;
input int                   InpMinSequenceLength                   = 50;
input double                InpMinSequenceConfidence               = 0.80;
input double                InpMinDominanceMargin                  = 0.00;

input ENUM_PG_RISK_MODE     InpRiskMode                            = PG_RISK_ADAPTIVE_COMPOUND;
input double                InpFixedLots                           = 0.01;
input double                InpRiskPercent                         = 0.50;
input double                InpMinRiskPercent                      = 0.05;
input double                InpMaxRiskPercent                      = 1.00;
input double                InpCompoundBoostPer10PctGrowth         = 0.10;
input double                InpAbsoluteMaxLots                     = 5.00;
input double                InpMinFreeMarginAfterTradePercent      = 75.0;
input double                InpMaxSpreadPips                       = 2.5;
input int                   InpSlippagePoints                      = 20;

input ENUM_PG_STOP_MODE     InpStopMode                            = PG_STOP_ATR;
input double                InpFixedStopLossPips                   = 12.0;
input int                   InpATRTimeframe                        = 0;
input int                   InpATRPeriod                           = 14;
input double                InpATRStopMultiplier                   = 1.8;
input double                InpMinStopLossPips                     = 5.0;
input double                InpMaxStopLossPips                     = 35.0;
input double                InpRewardRiskRatio                     = 1.6;
input double                InpMinTakeProfitPips                   = 6.0;
input double                InpStopLevelBufferPips                 = 1.0;
input bool                  InpECNFallbackAttachStopsAfterOpen     = true;
input bool                  InpRejectIfStopsCannotAttach           = true;

input int                   InpMaxOpenPositions                    = 1;
input int                   InpMaxTradesPerDay                     = 5;
input int                   InpMaxTradesPerWindow                  = 5;
input int                   InpTradeWindowMinutes                  = 45;
input int                   InpCooldownAfterWindowMinutes          = 45;
input int                   InpMinSecondsBetweenTrades             = 180;
input int                   InpLossStreakLimit                     = 3;
input int                   InpLossStreakCooldownMinutes           = 60;
input double                InpDailyLossLimitPercent               = 3.0;
input double                InpMaxEquityDrawdownPercent            = 8.0;
input bool                  InpCloseOwnedTradesOnEquityStop        = true;

input bool                  InpCloseOppositePositionsBeforeEntry   = true;
input bool                  InpCloseOnOppositePacket               = true;
input bool                  InpUseBreakEven                        = true;
input double                InpBreakEvenTriggerPips                = 8.0;
input double                InpBreakEvenLockPips                   = 1.0;
input bool                  InpUseTrailingStop                     = true;
input double                InpTrailingStartPips                   = 12.0;
input double                InpTrailingDistancePips                = 8.0;
input double                InpTrailingStepPips                    = 1.0;
input int                   InpMaxHoldMinutes                      = 0;

input bool                  InpUsePackageAwareManagement           = true;
input bool                  InpRequireKnownAllowancePackage        = true;
input bool                  InpAllowIntradayEnterNowPackages       = true;
input bool                  InpAllowSwingPackages                  = true;
input bool                  InpRequireProfessionalTradePlan        = true;
input int                   InpMinProfessionalCandles              = 8;
input int                   InpMinProfessionalExpectedMinutes      = 30;
input double                InpIntradayRiskPercent                 = 0.35;
input double                InpSwingRiskPercent                    = 0.50;
input double                InpIntradayMaxSpreadPips               = 2.0;
input double                InpSwingMaxSpreadPips                  = 2.8;
input double                InpIntradayMaxStopLossPips             = 18.0;
input double                InpSwingMaxStopLossPips                = 35.0;
input double                InpIntradayRewardRiskRatio             = 1.25;
input double                InpSwingRewardRiskRatio                = 1.80;
input int                   InpIntradayMaxHoldMinutes              = 12;
input int                   InpSwingMaxHoldMinutes                 = 0;
input double                InpIntradayBreakEvenTriggerPips        = 5.0;
input double                InpIntradayBreakEvenLockPips           = 0.5;
input double                InpSwingBreakEvenTriggerPips           = 10.0;
input double                InpSwingBreakEvenLockPips              = 1.2;
input double                InpIntradayTrailingStartPips           = 8.0;
input double                InpIntradayTrailingDistancePips        = 5.0;
input double                InpIntradayTrailingStepPips            = 0.8;
input double                InpSwingTrailingStartPips              = 16.0;
input double                InpSwingTrailingDistancePips           = 10.0;
input double                InpSwingTrailingStepPips               = 1.5;

input bool                  InpWriteAuditLog                       = true;
input string                InpAuditFile                           = "PhoenixGuard\\mt4_executioner_audit.csv";
input string                InpStateFile                           = "PhoenixGuard\\mt4_executioner_state.txt";

struct PgPacket
{
   bool     ok;
   string   reject;
   string   raw;
   string   packet_id;
   string   side;
   string   symbol;
   string   timeframe;
   int      frame_id;
   int      capture_count;
   int      state_version;
   datetime created_epoch;
   datetime valid_until_epoch;
   int      expiry_seconds;
   double   sequence_confidence;
   int      sequence_length;
   string   sequence_status;
   double   dominance_margin;
   bool     trade_permission_present;
   bool     trade_permission_allowed;
   string   allowance_package_type;
   string   allowance_family;
   string   allowance_selected_lane;
   string   allowance_timing_mode;
   bool     allowance_accepted;
   bool     allowance_execution_ready;
   bool     allowance_entry_now_allowed;
   bool     professional_grade;
   string   professional_authority_side;
   string   professional_thesis_state;
   string   professional_thesis_class;
   int      professional_expected_candles;
   int      professional_expected_duration_sec;
   int      professional_minimum_candles;
};

string   g_lastAcceptedPacketId = "";
string   g_lastSeenPacketId     = "";
string   g_lastStatus           = "";
int      g_lastAcceptedFrame    = -1;
int      g_lastAcceptedCapture  = -1;
int      g_lastAcceptedState    = -1;
uint     g_lastPollTick         = 0;
bool     g_polling              = false;
datetime g_dayStart             = 0;
double   g_dayStartEquity       = 0.0;
string   g_tradeSymbol          = "";

//+------------------------------------------------------------------+
//| Expert lifecycle                                                  |
//+------------------------------------------------------------------+
int OnInit()
{
   g_tradeSymbol = ResolveTradeSymbol();
   g_dayStart = DayStart(TimeCurrent());
   g_dayStartEquity = AccountEquity();
   LoadPersistentState();
   InitRiskWatermarks();

   if(MarketInfo(g_tradeSymbol, MODE_POINT) <= 0.0)
   {
      Print("PhoenixGuard init failed: invalid trade symbol ", g_tradeSymbol);
      return(INIT_FAILED);
   }

   bool timer_ok = false;
   if(InpPollMilliseconds < 1000)
      timer_ok = EventSetMillisecondTimer(MathMax(50, InpPollMilliseconds));
   else
      timer_ok = EventSetTimer(MathMax(1, InpPollMilliseconds / 1000));

   if(!timer_ok)
   {
      Print("PhoenixGuard init failed: timer setup error ", GetLastError());
      return(INIT_FAILED);
   }

   Print("PhoenixGuard MT4 Executioner v1.01 FILE-BRIDGE-ONLY armed on ", g_tradeSymbol,
         " source=CommonFileBridge",
         " dry_run=", BoolText(InpDryRun),
         " live_execution_input=", BoolText(InpAllowLiveExecution),
         " package_management=", BoolText(InpUsePackageAwareManagement));
   Print("PhoenixGuard bridge file: ", InpCommonSignalFile);
   Audit("INIT", "", "", 0.0, "EA initialized");
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   SavePersistentState();
   Audit("DEINIT", "", "", 0.0, "EA stopped reason=" + IntegerToString(reason));
}

void OnTick()
{
   ManageOpenTrades();
}

void OnTimer()
{
   ManageOpenTrades();

   uint now_tick = GetTickCount();
   if(g_lastPollTick != 0 && (int)(now_tick - g_lastPollTick) < MathMax(50, InpPollMilliseconds))
      return;
   g_lastPollTick = now_tick;

   if(g_polling)
      return;
   g_polling = true;
   PollAndProcess();
   g_polling = false;
}

//+------------------------------------------------------------------+
//| Polling and packet processing                                     |
//+------------------------------------------------------------------+
void PollAndProcess()
{
   string raw = "";
   string transport_reason = "";
   bool has_payload = FetchPacketPayload(raw, transport_reason);
   if(!has_payload)
   {
      SetStatus(transport_reason);
      return;
   }

   PgPacket packet;
   ResetPacket(packet);
   if(!ValidatePhoenixPacket(raw, packet))
   {
      SetStatus("packet rejected: " + packet.reject);
      Audit("REJECT_PACKET", packet.packet_id, packet.side, 0.0, packet.reject);
      return;
   }

   if(packet.packet_id == g_lastAcceptedPacketId || packet.packet_id == g_lastSeenPacketId)
   {
      SetStatus("duplicate packet ignored: " + packet.packet_id);
      return;
   }
   g_lastSeenPacketId = packet.packet_id;

   if(InpCloseOnOppositePacket || InpCloseOppositePositionsBeforeEntry)
      CloseOppositeOrders(packet.side, "opposite packet " + packet.packet_id);

   string safety_reason = "";
   if(!SafetyAllowsNewTrade(packet, safety_reason))
   {
      SetStatus("trade blocked: " + safety_reason);
      Audit("BLOCK_TRADE", packet.packet_id, packet.side, 0.0, safety_reason);
      return;
   }

   double sl_pips = ResolveStopLossPips(g_tradeSymbol, packet);
   double tp_pips = ResolveTakeProfitPips(sl_pips, packet);
   double lots = CalculateLots(g_tradeSymbol, packet.side, sl_pips, packet, safety_reason);
   if(lots <= 0.0)
   {
      SetStatus("lot sizing blocked: " + safety_reason);
      Audit("BLOCK_SIZE", packet.packet_id, packet.side, 0.0, safety_reason);
      return;
   }

   bool opened = OpenPacketTrade(packet, lots, sl_pips, tp_pips);
   if(opened)
   {
      g_lastAcceptedPacketId = packet.packet_id;
      g_lastAcceptedFrame = packet.frame_id;
      g_lastAcceptedCapture = packet.capture_count;
      g_lastAcceptedState = packet.state_version;
      SetGlobalDouble("last_trade_time", (double)TimeCurrent());
      SavePersistentState();
      Audit("ACCEPT_TRADE", packet.packet_id, packet.side, lots,
            "package=" + packet.allowance_package_type +
            " lane=" + packet.allowance_selected_lane +
            " timing=" + packet.allowance_timing_mode +
            " sl_pips=" + DoubleToString(sl_pips, 1) +
            " tp_pips=" + DoubleToString(tp_pips, 1));
   }
}

bool FetchPacketPayload(string &raw, string &reason)
{
   raw = "";
   reason = "";
   return FetchPacketFromCommonFile(raw, reason);
}

bool FetchPacketFromWeb(string &raw, string &reason)
{
   string url = BuildExecutionUrl();
   char post[];
   char result[];
   string result_headers = "";
   ArrayResize(post, 0);
   ArrayResize(result, 0);
   ResetLastError();
   int status = WebRequest("GET", url, "", "", MathMax(50, InpWebTimeoutMs), post, 0, result, result_headers);
   int err = GetLastError();
   if(status == 404)
   {
      reason = "no executable packet";
      return(false);
   }
   if(status == -1)
   {
      reason = "webrequest error " + IntegerToString(err) + " for " + url;
      return(false);
   }
   if(status < 200 || status >= 300)
   {
      reason = "http " + IntegerToString(status) + " from execution endpoint";
      return(false);
   }
   raw = CharArrayToString(result, 0, ArraySize(result));
   if(StringLen(Trim(raw)) <= 0)
   {
      reason = "empty execution endpoint response";
      return(false);
   }
   reason = "packet payload fetched";
   return(true);
}

bool FetchPacketFromCommonFile(string &raw, string &reason)
{
   raw = "";
   int attempts = InpCommonFileOpenRetries;
   if(attempts < 1)
      attempts = 1;
   int retry_delay_ms = InpCommonFileRetryDelayMs;
   if(retry_delay_ms < 0)
      retry_delay_ms = 0;

   int handle = INVALID_HANDLE;
   int last_error = 0;
   for(int attempt = 0; attempt < attempts; attempt++)
   {
      ResetLastError();
      handle = FileOpen(InpCommonSignalFile, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE, 0);
      if(handle != INVALID_HANDLE)
         break;
      last_error = GetLastError();
      if(attempt + 1 < attempts && retry_delay_ms > 0)
         Sleep(retry_delay_ms);
   }
   if(handle == INVALID_HANDLE)
   {
      reason = "signal file unavailable: " + InpCommonSignalFile + " err=" + IntegerToString(last_error) + " attempts=" + IntegerToString(attempts);
      return(false);
   }

   while(!FileIsEnding(handle))
   {
      string line = FileReadString(handle);
      raw += line;
      if(!FileIsEnding(handle))
         raw += "\n";
   }
   FileClose(handle);

   if(StringLen(Trim(raw)) <= 0)
   {
      reason = "signal file empty";
      return(false);
   }
   if(StringFind(raw, "NO_EXECUTION_PACKET", 0) >= 0)
   {
      reason = "no executable packet from bridge";
      return(false);
   }
   if(StringFind(raw, "BRIDGE_ERROR", 0) >= 0)
   {
      reason = "bridge error status";
      return(false);
   }
   reason = "packet payload fetched from common file";
   return(true);
}

string BuildExecutionUrl()
{
   if(StringLen(Trim(InpEndpointOverride)) > 0)
      return Trim(InpEndpointOverride);
   string base = Trim(InpBaseUrl);
   while(StringLen(base) > 0 && StringSubstr(base, StringLen(base) - 1, 1) == "/")
      base = StringSubstr(base, 0, StringLen(base) - 1);
   return base + "/v1/mobile/model-council/sessions/" + UrlEncodeLite(InpSessionId) + "/execution/latest";
}

bool ValidatePhoenixPacket(const string raw, PgPacket &packet)
{
   ResetPacket(packet);
   packet.raw = raw;

   string schema = "";
   if(!JsonGetString(raw, "schema_version", schema) || (schema != "PG_EXECUTION_PACKET_V3" && schema != "PG_MT4_EXECUTION_COMMAND_V1"))
      return Reject(packet, "INVALID_SCHEMA_VERSION");

   string packet_type = "";
   if(schema == "PG_EXECUTION_PACKET_V3" && JsonGetString(raw, "packet_type", packet_type) && packet_type != "PG_EXECUTION_PACKET_V3")
      return Reject(packet, "PACKET_TYPE_NOT_EXECUTION_PACKET");

   if(!JsonGetString(raw, "packet_id", packet.packet_id) || StringLen(packet.packet_id) <= 0)
      return Reject(packet, "MISSING_PACKET_ID");

   JsonGetString(raw, "symbol", packet.symbol);
   JsonGetString(raw, "timeframe", packet.timeframe);
   JsonGetInt(raw, "frame_id", packet.frame_id);
   JsonGetInt(raw, "capture_count", packet.capture_count);
   JsonGetInt(raw, "state_version", packet.state_version);

   double created = 0.0;
   double valid_until = 0.0;
   if(!JsonGetDouble(raw, "created_epoch_sec", created))
      JsonGetDouble(raw, "created_epoch", created);
   if(!JsonGetDouble(raw, "valid_until_epoch_sec", valid_until))
      JsonGetDouble(raw, "valid_until_epoch", valid_until);
   packet.created_epoch = (datetime)MathFloor(created);
   packet.valid_until_epoch = (datetime)MathFloor(valid_until);

   datetime now_gmt = TimeGMT();
   if(created <= 0.0)
      return Reject(packet, "MISSING_CREATED_EPOCH");
   if(valid_until <= 0.0)
      return Reject(packet, "MISSING_VALID_UNTIL_EPOCH");
   if(valid_until + (InpPacketExpiryGraceMs / 1000.0) <= (double)now_gmt)
      return Reject(packet, "PACKET_EXPIRED");
   double age_ms = ((double)now_gmt - created) * 1000.0;
   if(age_ms > InpPacketMaxAgeMs)
      return Reject(packet, "PACKET_TOO_OLD");
   if(age_ms < -(InpClockSkewToleranceSec * 1000.0))
      return Reject(packet, "PACKET_CLOCK_SKEW_FUTURE_CREATED");

   if(StringLen(Trim(InpExpectedTimeframe)) > 0)
   {
      if(Upper(Trim(packet.timeframe)) != Upper(Trim(InpExpectedTimeframe)))
         return Reject(packet, "TIMEFRAME_MISMATCH packet=" + packet.timeframe + " expected=" + InpExpectedTimeframe);
   }

   string live = JsonGetObject(raw, "live_integrity");
   if(StringLen(live) <= 0)
      return Reject(packet, "MISSING_LIVE_INTEGRITY");
   if(!JsonBoolEquals(live, "is_live", true))
      return Reject(packet, "NOT_LIVE");
   if(!JsonBoolEquals(live, "frame_advancing", true))
      return Reject(packet, "FRAME_NOT_ADVANCING");
   if(!JsonBoolEquals(live, "capture_advancing", true))
      return Reject(packet, "CAPTURE_NOT_ADVANCING");
   if(!JsonBoolEquals(live, "state_advancing", true))
      return Reject(packet, "STATE_NOT_ADVANCING");
   string cache_status = "";
   if(!JsonGetString(live, "cache_status", cache_status) || cache_status != "fresh")
      return Reject(packet, "CACHE_NOT_FRESH");
   string source = "";
   if(!JsonGetString(live, "source", source) || source != "model_council")
      return Reject(packet, "SOURCE_NOT_MODEL_COUNCIL");
   string input_hash = "";
   if(!JsonGetString(live, "input_frame_hash", input_hash) || StringLen(input_hash) <= 0)
      return Reject(packet, "MISSING_INPUT_FRAME_HASH");

   string execution = JsonGetObject(raw, "execution");
   if(StringLen(execution) <= 0)
      return Reject(packet, "MISSING_EXECUTION");
   if(!JsonBoolEquals(execution, "enabled", true))
      return Reject(packet, "EXECUTION_NOT_ENABLED");
   string execution_state = "";
   if(!JsonGetString(execution, "state", execution_state) || execution_state != "EXECUTABLE")
      return Reject(packet, "EXECUTION_STATE_NOT_EXECUTABLE");
   if(!JsonGetString(execution, "side", packet.side))
      return Reject(packet, "INVALID_OR_MISSING_EXECUTION_SIDE");
   packet.side = Upper(Trim(packet.side));
   if(packet.side != "BUY" && packet.side != "SELL")
      return Reject(packet, "INVALID_EXECUTION_SIDE_" + packet.side);
   string amount_action = "";
   if(!JsonGetString(execution, "amount_action", amount_action) || amount_action != "DO_NOT_CHANGE_AMOUNT")
      return Reject(packet, "AMOUNT_ACTION_NOT_LOCKED");
   if(!JsonGetInt(execution, "expiry_seconds", packet.expiry_seconds) || packet.expiry_seconds <= 0)
      return Reject(packet, "INVALID_OR_MISSING_EXPIRY_SECONDS");
   string time_sequence = JsonGetObject(execution, "time_sequence");
   int target_seconds = 0;
   if(StringLen(time_sequence) <= 0 || !JsonGetInt(time_sequence, "target_seconds", target_seconds) || target_seconds != packet.expiry_seconds)
      return Reject(packet, "TIME_SEQUENCE_EXPIRY_MISMATCH");

   string council = JsonGetObject(raw, "model_council");
   if(StringLen(council) <= 0)
      return Reject(packet, "MISSING_MODEL_COUNCIL");
   string final_state = "";
   string final_side = "";
   if(!JsonGetString(council, "final_state", final_state) || final_state != "EXECUTABLE")
      return Reject(packet, "COUNCIL_STATE_NOT_EXECUTABLE");
   if(!JsonGetString(council, "final_side", final_side) || Upper(Trim(final_side)) != packet.side)
      return Reject(packet, "EXECUTION_SIDE_MODEL_COUNCIL_MISMATCH");
   JsonGetDouble(council, "dominance_margin", packet.dominance_margin);
   if(InpMinDominanceMargin > 0.0 && packet.dominance_margin < InpMinDominanceMargin)
      return Reject(packet, "DOMINANCE_MARGIN_TOO_LOW");

   string health = JsonGetObject(raw, "runtime_model_health");
   if(StringLen(health) <= 0)
      return Reject(packet, "MISSING_RUNTIME_MODEL_HEALTH");
   if(!JsonBoolEquals(health, "all_required_models_awake", true))
      return Reject(packet, "REQUIRED_MODELS_NOT_AWAKE");

   string permission = JsonGetObject(raw, "trade_permission");
   if(StringLen(permission) <= 0)
      permission = JsonGetObject(council, "trade_permission");
   packet.trade_permission_present = (StringLen(permission) > 0);
   packet.trade_permission_allowed = false;
   if(packet.trade_permission_present)
      JsonGetBool(permission, "executable_allowed", packet.trade_permission_allowed);
   if(InpRequireTradePermission && (!packet.trade_permission_present || !packet.trade_permission_allowed))
      return Reject(packet, "TRADE_PERMISSION_DENIED_OR_MISSING");

   string allowance = JsonGetObject(raw, "allowance_package");
   if(StringLen(allowance) > 0)
   {
      JsonGetString(allowance, "package_type", packet.allowance_package_type);
      JsonGetString(allowance, "allowance_family", packet.allowance_family);
      JsonGetString(allowance, "selected_lane", packet.allowance_selected_lane);
      JsonGetString(allowance, "timing_mode", packet.allowance_timing_mode);
      JsonGetBool(allowance, "accepted", packet.allowance_accepted);
      JsonGetBool(allowance, "execution_ready", packet.allowance_execution_ready);
      JsonGetBool(allowance, "entry_now_allowed", packet.allowance_entry_now_allowed);
      packet.allowance_package_type = NormalizeAllowancePackageType(packet.allowance_package_type);
      packet.allowance_family = Upper(Trim(packet.allowance_family));
      packet.allowance_selected_lane = Upper(Trim(packet.allowance_selected_lane));
      packet.allowance_timing_mode = Upper(Trim(packet.allowance_timing_mode));

      string professional = JsonGetObject(allowance, "professional_trade_plan");
      if(StringLen(professional) > 0)
      {
         JsonGetBool(professional, "professional_grade", packet.professional_grade);
         JsonGetString(professional, "authority_side", packet.professional_authority_side);
         if(StringLen(packet.professional_authority_side) <= 0)
            JsonGetString(professional, "side", packet.professional_authority_side);
         JsonGetString(professional, "professional_thesis_state", packet.professional_thesis_state);
         JsonGetString(professional, "thesis_class", packet.professional_thesis_class);
         JsonGetInt(professional, "expected_candle_count", packet.professional_expected_candles);
         JsonGetInt(professional, "expected_duration_sec", packet.professional_expected_duration_sec);
         JsonGetInt(professional, "minimum_professional_candles", packet.professional_minimum_candles);
         packet.professional_authority_side = Upper(Trim(packet.professional_authority_side));
         packet.professional_thesis_state = Upper(Trim(packet.professional_thesis_state));
         packet.professional_thesis_class = Upper(Trim(packet.professional_thesis_class));
      }
   }
   if(StringLen(packet.allowance_package_type) <= 0)
      packet.allowance_package_type = "LEGACY_EXECUTION";
   if(StringLen(packet.allowance_family) <= 0)
      packet.allowance_family = (packet.allowance_package_type == "INTRADAY_ENTER_NOW" ? "INTRADAY" : "SWING");
   if(InpRequireKnownAllowancePackage && !IsKnownAllowancePackage(packet.allowance_package_type))
      return Reject(packet, "UNKNOWN_ALLOWANCE_PACKAGE_" + packet.allowance_package_type);
   if(!packet.allowance_accepted)
      return Reject(packet, "ALLOWANCE_PACKAGE_NOT_ACCEPTED");
   if(!packet.allowance_execution_ready)
      return Reject(packet, "ALLOWANCE_PACKAGE_NOT_EXECUTION_READY");
   if(packet.allowance_package_type == "INTRADAY_ENTER_NOW" && !InpAllowIntradayEnterNowPackages)
      return Reject(packet, "INTRADAY_ENTER_NOW_PACKAGE_DISABLED");
   if(packet.allowance_package_type == "SWING" && !InpAllowSwingPackages)
      return Reject(packet, "SWING_PACKAGE_DISABLED");
   if(packet.allowance_package_type == "INTRADAY_ENTER_NOW" && !packet.allowance_entry_now_allowed)
      return Reject(packet, "INTRADAY_PACKAGE_NOT_ENTRY_NOW_ALLOWED");
   if(InpRequireProfessionalTradePlan)
   {
      if(!packet.professional_grade)
         return Reject(packet, "PROFESSIONAL_TRADE_PLAN_NOT_GRADE_READY");
      if(packet.professional_authority_side != "BUY" && packet.professional_authority_side != "SELL")
         return Reject(packet, "PROFESSIONAL_AUTHORITY_SIDE_INVALID");
      if(packet.professional_authority_side != packet.side)
         return Reject(packet, "PROFESSIONAL_AUTHORITY_SIDE_MISMATCH");
      int min_professional_candles = MathMax(1, MathMax(InpMinProfessionalCandles, packet.professional_minimum_candles));
      if(packet.professional_expected_candles < min_professional_candles)
         return Reject(packet, "PROFESSIONAL_THESIS_CANDLES_TOO_SHORT");
      if(InpMinProfessionalExpectedMinutes > 0 && packet.professional_expected_duration_sec < InpMinProfessionalExpectedMinutes * 60)
         return Reject(packet, "PROFESSIONAL_THESIS_DURATION_TOO_SHORT");
   }

   if(InpRequireCompleteSequence)
   {
      string sequence = JsonGetObject(council, "sequence_context");
      if(StringLen(sequence) <= 0)
         return Reject(packet, "MISSING_SEQUENCE_CONTEXT");
      JsonGetString(sequence, "sequence_status", packet.sequence_status);
      if(StringLen(packet.sequence_status) <= 0)
         JsonGetString(sequence, "status", packet.sequence_status);
      packet.sequence_status = Upper(Trim(packet.sequence_status));
      JsonGetInt(sequence, "sequence_length", packet.sequence_length);
      JsonGetDouble(sequence, "sequence_confidence", packet.sequence_confidence);
      if(packet.sequence_status != "COMPLETE")
         return Reject(packet, "PARTIAL_SEQUENCE_NOT_EXECUTABLE");
      if(packet.sequence_length < InpMinSequenceLength)
         return Reject(packet, "SEQUENCE_LENGTH_TOO_LOW");
      if(packet.sequence_confidence < InpMinSequenceConfidence)
         return Reject(packet, "SEQUENCE_CONFIDENCE_TOO_LOW");
   }

   if(packet.frame_id <= g_lastAcceptedFrame && packet.capture_count <= g_lastAcceptedCapture && packet.state_version <= g_lastAcceptedState)
      return Reject(packet, "IDENTITY_NOT_ADVANCING_FROM_LAST_ACCEPTED");

   packet.ok = true;
   packet.reject = "OK";
   return(true);
}

bool Reject(PgPacket &packet, const string reason)
{
   packet.ok = false;
   packet.reject = reason;
   return(false);
}

void ResetPacket(PgPacket &packet)
{
   packet.ok = false;
   packet.reject = "";
   packet.raw = "";
   packet.packet_id = "";
   packet.side = "";
   packet.symbol = "";
   packet.timeframe = "";
   packet.frame_id = -1;
   packet.capture_count = -1;
   packet.state_version = -1;
   packet.created_epoch = 0;
   packet.valid_until_epoch = 0;
   packet.expiry_seconds = 0;
   packet.sequence_confidence = 0.0;
   packet.sequence_length = 0;
   packet.sequence_status = "";
   packet.dominance_margin = 0.0;
   packet.trade_permission_present = false;
   packet.trade_permission_allowed = false;
   packet.allowance_package_type = "";
   packet.allowance_family = "";
   packet.allowance_selected_lane = "";
   packet.allowance_timing_mode = "";
   packet.allowance_accepted = false;
   packet.allowance_execution_ready = false;
   packet.allowance_entry_now_allowed = false;
   packet.professional_grade = false;
   packet.professional_authority_side = "";
   packet.professional_thesis_state = "";
   packet.professional_thesis_class = "";
   packet.professional_expected_candles = 0;
   packet.professional_expected_duration_sec = 0;
   packet.professional_minimum_candles = 0;
}

//+------------------------------------------------------------------+
//| Trade execution                                                   |
//+------------------------------------------------------------------+
bool OpenPacketTrade(PgPacket &packet, const double lots, const double sl_pips, const double tp_pips)
{
   if(InpDryRun || !InpAllowLiveExecution)
   {
      Print("PhoenixGuard DRY RUN accepted packet ", packet.packet_id, " side=", packet.side,
            " package=", packet.allowance_package_type, " lane=", packet.allowance_selected_lane,
            " timing=", packet.allowance_timing_mode,
            " thesis=", packet.professional_thesis_state,
            " expected_candles=", IntegerToString(packet.professional_expected_candles),
            " lots=", DoubleToString(lots, 2), " sl=", DoubleToString(sl_pips, 1), " tp=", DoubleToString(tp_pips, 1));
      return(true);
   }

   if(!IsTradeAllowed())
   {
      Print("PhoenixGuard trade denied by MT4 IsTradeAllowed().");
      return(false);
   }

   int cmd = (packet.side == "BUY" ? OP_BUY : OP_SELL);
   double price = EntryPrice(g_tradeSymbol, cmd);
   if(price <= 0.0)
      return(false);

   double sl = StopLossPrice(g_tradeSymbol, cmd, price, sl_pips);
   double tp = TakeProfitPrice(g_tradeSymbol, cmd, price, tp_pips);
   string comment = BuildOrderComment(packet.packet_id, packet.allowance_package_type);
   color arrow = (cmd == OP_BUY ? clrDodgerBlue : clrRed);

   int ticket = -1;
   bool sent_with_stops = true;
   for(int attempt = 0; attempt < 3; attempt++)
   {
      RefreshRates();
      price = EntryPrice(g_tradeSymbol, cmd);
      sl = StopLossPrice(g_tradeSymbol, cmd, price, sl_pips);
      tp = TakeProfitPrice(g_tradeSymbol, cmd, price, tp_pips);
      ResetLastError();
      ticket = OrderSend(g_tradeSymbol, cmd, lots, NormalizePrice(g_tradeSymbol, price), InpSlippagePoints,
                         NormalizePrice(g_tradeSymbol, sl), NormalizePrice(g_tradeSymbol, tp),
                         comment, InpMagicNumber, 0, arrow);
      int err = GetLastError();
      if(ticket > 0)
         break;

      if((err == 130 || err == 131) && InpECNFallbackAttachStopsAfterOpen)
      {
         sent_with_stops = false;
         ResetLastError();
         ticket = OrderSend(g_tradeSymbol, cmd, lots, NormalizePrice(g_tradeSymbol, price), InpSlippagePoints,
                            0.0, 0.0, comment, InpMagicNumber, 0, arrow);
         if(ticket > 0)
            break;
      }

      if(err == 136 || err == 138 || err == 146)
      {
         Sleep(150 + attempt * 150);
         continue;
      }
      Print("PhoenixGuard OrderSend failed err=", err, " ", TradeErrorText(err));
      return(false);
   }

   if(ticket <= 0)
   {
      Print("PhoenixGuard OrderSend failed after retries err=", GetLastError());
      return(false);
   }

   bool stops_ok = true;
   if(!sent_with_stops || !OrderHasStops(ticket))
      stops_ok = AttachStops(ticket, sl_pips, tp_pips);

   if(!stops_ok)
   {
      Print("PhoenixGuard could not attach protective stops for ticket ", ticket);
      if(InpRejectIfStopsCannotAttach)
      {
         CloseTicket(ticket, "protective stops failed");
         return(false);
      }
   }

   Print("PhoenixGuard opened ticket ", ticket, " packet=", packet.packet_id, " side=", packet.side,
         " package=", packet.allowance_package_type, " lane=", packet.allowance_selected_lane,
         " timing=", packet.allowance_timing_mode,
         " thesis=", packet.professional_thesis_state,
         " expected_candles=", IntegerToString(packet.professional_expected_candles),
         " lots=", DoubleToString(lots, 2));
   return(true);
}

bool AttachStops(const int ticket, const double sl_pips, const double tp_pips)
{
   for(int attempt = 0; attempt < 5; attempt++)
   {
      if(!OrderSelect(ticket, SELECT_BY_TICKET))
         return(false);
      int cmd = OrderType();
      double open_price = OrderOpenPrice();
      double sl = StopLossPrice(OrderSymbol(), cmd, open_price, sl_pips);
      double tp = TakeProfitPrice(OrderSymbol(), cmd, open_price, tp_pips);
      ResetLastError();
      bool ok = OrderModify(ticket, OrderOpenPrice(), NormalizePrice(OrderSymbol(), sl), NormalizePrice(OrderSymbol(), tp), 0, clrNONE);
      if(ok)
         return(true);
      int err = GetLastError();
      if(err == 130 || err == 136 || err == 138 || err == 146)
      {
         Sleep(200 + attempt * 150);
         continue;
      }
      Print("PhoenixGuard OrderModify attach stops failed ticket=", ticket, " err=", err, " ", TradeErrorText(err));
      return(false);
   }
   return(false);
}

bool OrderHasStops(const int ticket)
{
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
      return(false);
   return(OrderStopLoss() > 0.0 && OrderTakeProfit() > 0.0);
}

void ManageOpenTrades()
{
   if(EquityStopTriggered())
   {
      if(InpCloseOwnedTradesOnEquityStop)
         CloseAllOwnedOrders("equity/daily stop");
      return;
   }

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(!IsOwnedMarketOrder())
         continue;

      string symbol = OrderSymbol();
      int type = OrderType();
      double pips = CurrentOrderPips();
      string package_type = OrderAllowancePackageType();
      int max_hold_minutes = PackageMaxHoldMinutes(package_type);
      double break_even_trigger = PackageBreakEvenTriggerPips(package_type);
      double trailing_start = PackageTrailingStartPips(package_type);

      if(max_hold_minutes > 0 && (TimeCurrent() - OrderOpenTime()) >= max_hold_minutes * 60)
      {
         CloseTicket(OrderTicket(), "max hold minutes package=" + package_type);
         continue;
      }

      if(InpUseBreakEven && pips >= break_even_trigger)
         ApplyBreakEven(OrderTicket(), type, symbol, package_type);

      if(InpUseTrailingStop && pips >= trailing_start)
         ApplyTrailingStop(OrderTicket(), type, symbol, package_type);
   }
}

void ApplyBreakEven(const int ticket, const int type, const string symbol, const string package_type)
{
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
      return;
   double pip = PipSize(symbol);
   double lock_pips = PackageBreakEvenLockPips(package_type);
   double new_sl = 0.0;
   if(type == OP_BUY)
   {
      new_sl = OrderOpenPrice() + lock_pips * pip;
      if(OrderStopLoss() >= new_sl)
         return;
   }
   else if(type == OP_SELL)
   {
      new_sl = OrderOpenPrice() - lock_pips * pip;
      if(OrderStopLoss() > 0.0 && OrderStopLoss() <= new_sl)
         return;
   }
   else
      return;

   SafeModifyStop(ticket, new_sl, OrderTakeProfit(), "break-even " + package_type);
}

void ApplyTrailingStop(const int ticket, const int type, const string symbol, const string package_type)
{
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
      return;
   double pip = PipSize(symbol);
   double bid = MarketInfo(symbol, MODE_BID);
   double ask = MarketInfo(symbol, MODE_ASK);
   double trailing_distance = PackageTrailingDistancePips(package_type);
   double trailing_step = PackageTrailingStepPips(package_type);
   double new_sl = 0.0;
   if(type == OP_BUY)
   {
      new_sl = bid - trailing_distance * pip;
      if(OrderStopLoss() > 0.0 && new_sl <= OrderStopLoss() + trailing_step * pip)
         return;
   }
   else if(type == OP_SELL)
   {
      new_sl = ask + trailing_distance * pip;
      if(OrderStopLoss() > 0.0 && new_sl >= OrderStopLoss() - trailing_step * pip)
         return;
   }
   else
      return;

   SafeModifyStop(ticket, new_sl, OrderTakeProfit(), "trailing " + package_type);
}

bool SafeModifyStop(const int ticket, const double stop_loss, const double take_profit, const string context)
{
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
      return(false);
   string symbol = OrderSymbol();
   if(IsInsideFreezeLevel(symbol, OrderType(), stop_loss))
      return(false);
   ResetLastError();
   bool ok = OrderModify(ticket, OrderOpenPrice(), NormalizePrice(symbol, stop_loss), NormalizePrice(symbol, take_profit), 0, clrNONE);
   if(!ok)
   {
      int err = GetLastError();
      if(err != 1)
         Print("PhoenixGuard ", context, " modify failed ticket=", ticket, " err=", err, " ", TradeErrorText(err));
   }
   return(ok);
}

void CloseOppositeOrders(const string packet_side, const string reason)
{
   int opposite = (packet_side == "BUY" ? OP_SELL : OP_BUY);
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(!IsOwnedMarketOrder())
         continue;
      if(OrderType() == opposite)
         CloseTicket(OrderTicket(), reason);
   }
}

void CloseAllOwnedOrders(const string reason)
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(!IsOwnedMarketOrder())
         continue;
      CloseTicket(OrderTicket(), reason);
   }
}

bool CloseTicket(const int ticket, const string reason)
{
   for(int attempt = 0; attempt < 3; attempt++)
   {
      if(!OrderSelect(ticket, SELECT_BY_TICKET))
         return(false);
      int type = OrderType();
      string symbol = OrderSymbol();
      double close_price = (type == OP_BUY ? MarketInfo(symbol, MODE_BID) : MarketInfo(symbol, MODE_ASK));
      ResetLastError();
      bool ok = OrderClose(ticket, OrderLots(), NormalizePrice(symbol, close_price), InpSlippagePoints, clrOrange);
      if(ok)
      {
         Audit("CLOSE", "", (type == OP_BUY ? "BUY" : "SELL"), OrderLots(), reason);
         return(true);
      }
      int err = GetLastError();
      if(err == 136 || err == 138 || err == 146)
      {
         Sleep(150 + attempt * 150);
         continue;
      }
      Print("PhoenixGuard close failed ticket=", ticket, " err=", err, " ", TradeErrorText(err), " reason=", reason);
      return(false);
   }
   return(false);
}

//+------------------------------------------------------------------+
//| Safety and risk                                                   |
//+------------------------------------------------------------------+
bool SafetyAllowsNewTrade(PgPacket &packet, string &reason)
{
   reason = "";
   if(EquityStopTriggered())
   {
      reason = "equity/daily stop active";
      return(false);
   }

   if(IsCooldownActive(reason))
      return(false);

   if(!MarketTradeAllowed(g_tradeSymbol))
   {
      reason = "symbol trading not allowed";
      return(false);
   }

   if(!PackageAllowsNewTrade(packet, reason))
      return(false);

   double spread = SpreadPips(g_tradeSymbol);
   double max_spread = PackageMaxSpreadPips(packet.allowance_package_type);
   if(spread > max_spread)
   {
      reason = "spread too high " + DoubleToString(spread, 1) + " pips for package " + packet.allowance_package_type + " max=" + DoubleToString(max_spread, 1);
      return(false);
   }

   if(CountOpenOwnedOrders() >= InpMaxOpenPositions)
   {
      reason = "max open positions reached";
      return(false);
   }

   if(TradesOpenedToday() >= InpMaxTradesPerDay)
   {
      reason = "max trades per day reached";
      return(false);
   }

   if(TradesOpenedInWindow(InpTradeWindowMinutes) >= InpMaxTradesPerWindow)
   {
      datetime until = TimeCurrent() + InpCooldownAfterWindowMinutes * 60;
      SetGlobalDouble("cooldown_until", (double)until);
      reason = "trade window limit reached, cooldown until " + TimeToString(until, TIME_DATE | TIME_SECONDS);
      return(false);
   }

   datetime last_trade = (datetime)GetGlobalDouble("last_trade_time", 0.0);
   if(last_trade > 0 && TimeCurrent() - last_trade < InpMinSecondsBetweenTrades)
   {
      reason = "minimum trade spacing active";
      return(false);
   }

   int streak = CurrentLossStreak();
   if(streak >= InpLossStreakLimit)
   {
      datetime loss_until = TimeCurrent() + InpLossStreakCooldownMinutes * 60;
      SetGlobalDouble("cooldown_until", (double)loss_until);
      reason = "loss streak cooldown active";
      return(false);
   }

   return(true);
}

bool EquityStopTriggered()
{
   RefreshRiskWatermarks();
   double equity = AccountEquity();
   double day_loss_limit = g_dayStartEquity * InpDailyLossLimitPercent / 100.0;
   if(InpDailyLossLimitPercent > 0.0 && DailyNetProfitIncludingOpen() <= -day_loss_limit)
      return(true);

   double high = GetGlobalDouble("equity_high_water", equity);
   if(high <= 0.0)
      high = equity;
   double dd_pct = 100.0 * (high - equity) / high;
   if(InpMaxEquityDrawdownPercent > 0.0 && dd_pct >= InpMaxEquityDrawdownPercent)
      return(true);
   return(false);
}

bool IsCooldownActive(string &reason)
{
   datetime until = (datetime)GetGlobalDouble("cooldown_until", 0.0);
   if(until > TimeCurrent())
   {
      reason = "cooldown until " + TimeToString(until, TIME_DATE | TIME_SECONDS);
      return(true);
   }
   return(false);
}

bool PackageAllowsNewTrade(PgPacket &packet, string &reason)
{
   string package_type = NormalizeAllowancePackageType(packet.allowance_package_type);
   if(InpRequireKnownAllowancePackage && !IsKnownAllowancePackage(package_type))
   {
      reason = "unknown allowance package " + package_type;
      return(false);
   }
   if(package_type == "INTRADAY_ENTER_NOW" && !InpAllowIntradayEnterNowPackages)
   {
      reason = "intraday enter-now package disabled";
      return(false);
   }
   if(package_type == "SWING" && !InpAllowSwingPackages)
   {
      reason = "swing package disabled";
      return(false);
   }
   if(package_type == "INTRADAY_ENTER_NOW" && !packet.allowance_entry_now_allowed)
   {
      reason = "intraday package not marked entry_now_allowed";
      return(false);
   }
   return(true);
}

double PacketRiskPercent(PgPacket &packet)
{
   double base = InpRiskPercent;
   if(InpUsePackageAwareManagement)
   {
      if(packet.allowance_package_type == "INTRADAY_ENTER_NOW" && InpIntradayRiskPercent > 0.0)
         base = InpIntradayRiskPercent;
      else if(packet.allowance_package_type == "SWING" && InpSwingRiskPercent > 0.0)
         base = InpSwingRiskPercent;
   }
   return(AdaptiveRiskPercentFromBase(base));
}

double PackageMaxSpreadPips(const string package_type)
{
   string normalized = NormalizeAllowancePackageType(package_type);
   if(InpUsePackageAwareManagement)
   {
      if(normalized == "INTRADAY_ENTER_NOW" && InpIntradayMaxSpreadPips > 0.0)
         return(InpIntradayMaxSpreadPips);
      if(normalized == "SWING" && InpSwingMaxSpreadPips > 0.0)
         return(InpSwingMaxSpreadPips);
   }
   return(InpMaxSpreadPips);
}

double PackageMaxStopLossPips(const string package_type)
{
   string normalized = NormalizeAllowancePackageType(package_type);
   double max_sl = InpMaxStopLossPips;
   if(InpUsePackageAwareManagement)
   {
      if(normalized == "INTRADAY_ENTER_NOW" && InpIntradayMaxStopLossPips > 0.0)
         max_sl = MathMin(max_sl, InpIntradayMaxStopLossPips);
      else if(normalized == "SWING" && InpSwingMaxStopLossPips > 0.0)
         max_sl = MathMin(max_sl, InpSwingMaxStopLossPips);
   }
   return(max_sl);
}

double PackageRewardRiskRatio(const string package_type)
{
   string normalized = NormalizeAllowancePackageType(package_type);
   if(InpUsePackageAwareManagement)
   {
      if(normalized == "INTRADAY_ENTER_NOW" && InpIntradayRewardRiskRatio > 0.0)
         return(InpIntradayRewardRiskRatio);
      if(normalized == "SWING" && InpSwingRewardRiskRatio > 0.0)
         return(InpSwingRewardRiskRatio);
   }
   return(InpRewardRiskRatio);
}

int PackageMaxHoldMinutes(const string package_type)
{
   string normalized = NormalizeAllowancePackageType(package_type);
   if(InpUsePackageAwareManagement)
   {
      if(normalized == "INTRADAY_ENTER_NOW")
         return(InpIntradayMaxHoldMinutes);
      if(normalized == "SWING")
         return(InpSwingMaxHoldMinutes);
   }
   return(InpMaxHoldMinutes);
}

double PackageBreakEvenTriggerPips(const string package_type)
{
   string normalized = NormalizeAllowancePackageType(package_type);
   if(InpUsePackageAwareManagement)
   {
      if(normalized == "INTRADAY_ENTER_NOW" && InpIntradayBreakEvenTriggerPips > 0.0)
         return(InpIntradayBreakEvenTriggerPips);
      if(normalized == "SWING" && InpSwingBreakEvenTriggerPips > 0.0)
         return(InpSwingBreakEvenTriggerPips);
   }
   return(InpBreakEvenTriggerPips);
}

double PackageBreakEvenLockPips(const string package_type)
{
   string normalized = NormalizeAllowancePackageType(package_type);
   if(InpUsePackageAwareManagement)
   {
      if(normalized == "INTRADAY_ENTER_NOW" && InpIntradayBreakEvenLockPips >= 0.0)
         return(InpIntradayBreakEvenLockPips);
      if(normalized == "SWING" && InpSwingBreakEvenLockPips >= 0.0)
         return(InpSwingBreakEvenLockPips);
   }
   return(InpBreakEvenLockPips);
}

double PackageTrailingStartPips(const string package_type)
{
   string normalized = NormalizeAllowancePackageType(package_type);
   if(InpUsePackageAwareManagement)
   {
      if(normalized == "INTRADAY_ENTER_NOW" && InpIntradayTrailingStartPips > 0.0)
         return(InpIntradayTrailingStartPips);
      if(normalized == "SWING" && InpSwingTrailingStartPips > 0.0)
         return(InpSwingTrailingStartPips);
   }
   return(InpTrailingStartPips);
}

double PackageTrailingDistancePips(const string package_type)
{
   string normalized = NormalizeAllowancePackageType(package_type);
   if(InpUsePackageAwareManagement)
   {
      if(normalized == "INTRADAY_ENTER_NOW" && InpIntradayTrailingDistancePips > 0.0)
         return(InpIntradayTrailingDistancePips);
      if(normalized == "SWING" && InpSwingTrailingDistancePips > 0.0)
         return(InpSwingTrailingDistancePips);
   }
   return(InpTrailingDistancePips);
}

double PackageTrailingStepPips(const string package_type)
{
   string normalized = NormalizeAllowancePackageType(package_type);
   if(InpUsePackageAwareManagement)
   {
      if(normalized == "INTRADAY_ENTER_NOW" && InpIntradayTrailingStepPips > 0.0)
         return(InpIntradayTrailingStepPips);
      if(normalized == "SWING" && InpSwingTrailingStepPips > 0.0)
         return(InpSwingTrailingStepPips);
   }
   return(InpTrailingStepPips);
}

double CalculateLots(const string symbol, const string side, const double sl_pips, PgPacket &packet, string &reason)
{
   reason = "";
   double lots = InpFixedLots;
   if(InpRiskMode != PG_RISK_FIXED_LOT)
   {
      double risk_pct = PacketRiskPercent(packet);
      double risk_base = (InpRiskMode == PG_RISK_BALANCE_PERCENT ? AccountBalance() : AccountEquity());
      double risk_money = risk_base * risk_pct / 100.0;
      double pip_value = PipValuePerLot(symbol);
      if(pip_value <= 0.0 || sl_pips <= 0.0)
      {
         reason = "pip value or stop distance invalid";
         return(0.0);
      }
      lots = risk_money / (sl_pips * pip_value);
   }

   if(InpAbsoluteMaxLots > 0.0)
      lots = MathMin(lots, InpAbsoluteMaxLots);
   lots = NormalizeLots(symbol, lots);

   double min_lot = MarketInfo(symbol, MODE_MINLOT);
   if(lots <= 0.0 || lots < min_lot)
   {
      reason = "calculated lots below broker minimum";
      return(0.0);
   }

   int cmd = (side == "BUY" ? OP_BUY : OP_SELL);
   ResetLastError();
   double margin_after = AccountFreeMarginCheck(symbol, cmd, lots);
   int err = GetLastError();
   if(margin_after <= 0.0 || err == 134)
   {
      reason = "insufficient margin";
      return(0.0);
   }
   double min_free = AccountEquity() * InpMinFreeMarginAfterTradePercent / 100.0;
   if(margin_after < min_free)
   {
      reason = "free margin after trade below configured safety floor";
      return(0.0);
   }
   return(lots);
}

double AdaptiveRiskPercent()
{
   return(AdaptiveRiskPercentFromBase(InpRiskPercent));
}

double AdaptiveRiskPercentFromBase(const double base_risk_percent)
{
   double risk = base_risk_percent;
   if(InpRiskMode == PG_RISK_ADAPTIVE_COMPOUND)
   {
      double initial = GetGlobalDouble("initial_equity", AccountEquity());
      double equity = AccountEquity();
      if(initial > 0.0 && equity > initial)
      {
         double growth_pct = 100.0 * (equity - initial) / initial;
         risk += (growth_pct / 10.0) * InpCompoundBoostPer10PctGrowth;
      }

      double high = GetGlobalDouble("equity_high_water", equity);
      if(high > 0.0 && equity < high)
      {
         double dd_pct = 100.0 * (high - equity) / high;
         double reduction = MathMax(0.25, 1.0 - (dd_pct / MathMax(1.0, InpMaxEquityDrawdownPercent)));
         risk *= reduction;
      }

      int streak = CurrentLossStreak();
      if(streak > 0)
         risk *= MathMax(0.25, 1.0 - 0.25 * streak);
   }
   risk = MathMax(InpMinRiskPercent, MathMin(InpMaxRiskPercent, risk));
   return(risk);
}

double ResolveStopLossPips(const string symbol, PgPacket &packet)
{
   double sl = InpFixedStopLossPips;
   if(InpStopMode == PG_STOP_ATR)
   {
      int tf = InpATRTimeframe;
      if(tf <= 0)
         tf = Period();
      double atr = iATR(symbol, tf, MathMax(2, InpATRPeriod), 0);
      if(atr > 0.0)
         sl = (atr / PipSize(symbol)) * InpATRStopMultiplier;
   }
   sl = MathMax(sl, InpMinStopLossPips);
   sl = MathMin(sl, PackageMaxStopLossPips(packet.allowance_package_type));
   sl = MathMax(sl, MinStopDistancePips(symbol));
   return(sl);
}

double ResolveTakeProfitPips(const double sl_pips, PgPacket &packet)
{
   return(MathMax(InpMinTakeProfitPips, sl_pips * PackageRewardRiskRatio(packet.allowance_package_type)));
}

//+------------------------------------------------------------------+
//| Order/account metrics                                             |
//+------------------------------------------------------------------+
bool IsOwnedMarketOrder()
{
   if(OrderMagicNumber() != InpMagicNumber)
      return(false);
   if(OrderSymbol() != g_tradeSymbol)
      return(false);
   int type = OrderType();
   return(type == OP_BUY || type == OP_SELL);
}

int CountOpenOwnedOrders()
{
   int count = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(IsOwnedMarketOrder())
         count++;
   }
   return(count);
}

int TradesOpenedToday()
{
   return(TradesOpenedSince(g_dayStart));
}

int TradesOpenedInWindow(const int minutes)
{
   datetime since = TimeCurrent() - MathMax(1, minutes) * 60;
   return(TradesOpenedSince(since));
}

int TradesOpenedSince(const datetime since_time)
{
   int count = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES) && IsOwnedMarketOrder() && OrderOpenTime() >= since_time)
         count++;
   }
   for(int h = OrdersHistoryTotal() - 1; h >= 0; h--)
   {
      if(!OrderSelect(h, SELECT_BY_POS, MODE_HISTORY))
         continue;
      if(OrderMagicNumber() != InpMagicNumber || OrderSymbol() != g_tradeSymbol)
         continue;
      int type = OrderType();
      if((type == OP_BUY || type == OP_SELL) && OrderOpenTime() >= since_time)
         count++;
   }
   return(count);
}

int CurrentLossStreak()
{
   int streak = 0;
   datetime cursor = TimeCurrent() + 86400;
   for(int step = 0; step < 20; step++)
   {
      datetime latest_close = 0;
      int latest_index = -1;
      for(int i = OrdersHistoryTotal() - 1; i >= 0; i--)
      {
         if(!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY))
            continue;
         if(OrderMagicNumber() != InpMagicNumber || OrderSymbol() != g_tradeSymbol)
            continue;
         int type = OrderType();
         if(type != OP_BUY && type != OP_SELL)
            continue;
         datetime closed = OrderCloseTime();
         if(closed > latest_close && closed < cursor)
         {
            latest_close = closed;
            latest_index = i;
         }
      }
      if(latest_index < 0)
         break;
      if(!OrderSelect(latest_index, SELECT_BY_POS, MODE_HISTORY))
         break;
      double pnl = OrderProfit() + OrderSwap() + OrderCommission();
      if(pnl < 0.0)
         streak++;
      else
         break;
      cursor = latest_close;
   }
   return(streak);
}

double DailyNetProfitIncludingOpen()
{
   double total = 0.0;
   for(int i = OrdersHistoryTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY))
         continue;
      if(OrderMagicNumber() != InpMagicNumber || OrderSymbol() != g_tradeSymbol)
         continue;
      int type = OrderType();
      if(type != OP_BUY && type != OP_SELL)
         continue;
      if(OrderCloseTime() >= g_dayStart)
         total += OrderProfit() + OrderSwap() + OrderCommission();
   }
   for(int j = OrdersTotal() - 1; j >= 0; j--)
   {
      if(!OrderSelect(j, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(IsOwnedMarketOrder())
         total += OrderProfit() + OrderSwap() + OrderCommission();
   }
   return(total);
}

double CurrentOrderPips()
{
   string symbol = OrderSymbol();
   double pip = PipSize(symbol);
   if(OrderType() == OP_BUY)
      return((MarketInfo(symbol, MODE_BID) - OrderOpenPrice()) / pip);
   if(OrderType() == OP_SELL)
      return((OrderOpenPrice() - MarketInfo(symbol, MODE_ASK)) / pip);
   return(0.0);
}

//+------------------------------------------------------------------+
//| Price, symbol, broker helpers                                     |
//+------------------------------------------------------------------+
string ResolveTradeSymbol()
{
   string s = Trim(InpTradeSymbol);
   if(StringLen(s) <= 0)
      s = Symbol();
   return(s);
}

bool PacketSymbolMatches(const string packet_symbol)
{
   string expected = Trim(InpTrackerSymbol);
   if(StringLen(expected) <= 0)
      expected = g_tradeSymbol;
   string p = NormalizeSymbolToken(packet_symbol);
   string e = NormalizeSymbolToken(expected);
   if(StringLen(p) <= 0 || StringLen(e) <= 0)
      return(false);
   if(p == e)
      return(true);
   if(StringFind(e, p, 0) == 0)
      return(true);
   if(StringFind(p, e, 0) == 0)
      return(true);
   return(false);
}

string NormalizeSymbolToken(string value)
{
   string s = Upper(value);
   StringReplace(s, "OTC", "");
   string out = "";
   for(int i = 0; i < StringLen(s); i++)
   {
      int c = StringGetChar(s, i);
      if((c >= 65 && c <= 90) || (c >= 48 && c <= 57))
         out += StringSubstr(s, i, 1);
   }
   return(out);
}

bool MarketTradeAllowed(const string symbol)
{
   return(MarketInfo(symbol, MODE_TRADEALLOWED) > 0.0);
}

double EntryPrice(const string symbol, const int cmd)
{
   if(cmd == OP_BUY)
      return(MarketInfo(symbol, MODE_ASK));
   if(cmd == OP_SELL)
      return(MarketInfo(symbol, MODE_BID));
   return(0.0);
}

double StopLossPrice(const string symbol, const int cmd, const double entry, const double sl_pips)
{
   double pip = PipSize(symbol);
   if(cmd == OP_BUY)
      return(entry - sl_pips * pip);
   return(entry + sl_pips * pip);
}

double TakeProfitPrice(const string symbol, const int cmd, const double entry, const double tp_pips)
{
   double pip = PipSize(symbol);
   if(cmd == OP_BUY)
      return(entry + tp_pips * pip);
   return(entry - tp_pips * pip);
}

double NormalizePrice(const string symbol, const double price)
{
   return(NormalizeDouble(price, (int)MarketInfo(symbol, MODE_DIGITS)));
}

double PipSize(const string symbol)
{
   double point = MarketInfo(symbol, MODE_POINT);
   int digits = (int)MarketInfo(symbol, MODE_DIGITS);
   if(digits == 3 || digits == 5)
      return(point * 10.0);
   return(point);
}

double PipValuePerLot(const string symbol)
{
   double tick_value = MarketInfo(symbol, MODE_TICKVALUE);
   double tick_size = MarketInfo(symbol, MODE_TICKSIZE);
   double pip = PipSize(symbol);
   if(tick_size <= 0.0)
      return(0.0);
   return(tick_value * (pip / tick_size));
}

double SpreadPips(const string symbol)
{
   double ask = MarketInfo(symbol, MODE_ASK);
   double bid = MarketInfo(symbol, MODE_BID);
   return((ask - bid) / PipSize(symbol));
}

double MinStopDistancePips(const string symbol)
{
   double point = MarketInfo(symbol, MODE_POINT);
   double stop_level = MarketInfo(symbol, MODE_STOPLEVEL);
   double min_pips = (stop_level * point) / PipSize(symbol);
   return(min_pips + InpStopLevelBufferPips);
}

bool IsInsideFreezeLevel(const string symbol, const int type, const double stop_loss)
{
   double freeze_points = MarketInfo(symbol, MODE_FREEZELEVEL);
   if(freeze_points <= 0.0 || stop_loss <= 0.0)
      return(false);
   double point = MarketInfo(symbol, MODE_POINT);
   double market = (type == OP_BUY ? MarketInfo(symbol, MODE_BID) : MarketInfo(symbol, MODE_ASK));
   return(MathAbs(market - stop_loss) <= freeze_points * point);
}

double NormalizeLots(const string symbol, double lots)
{
   double min_lot = MarketInfo(symbol, MODE_MINLOT);
   double max_lot = MarketInfo(symbol, MODE_MAXLOT);
   double step = MarketInfo(symbol, MODE_LOTSTEP);
   if(step <= 0.0)
      step = 0.01;
   if(lots < min_lot)
      return(0.0);
   lots = MathMin(max_lot, lots);
   lots = MathFloor(lots / step) * step;
   if(lots < min_lot)
      return(0.0);
   int digits = LotDigits(step);
   return(NormalizeDouble(lots, digits));
}

int LotDigits(const double step)
{
   for(int digits = 0; digits <= 8; digits++)
   {
      double scaled = step * MathPow(10.0, digits);
      if(MathAbs(scaled - MathRound(scaled)) < 0.0000001)
         return(digits);
   }
   return(2);
}

//+------------------------------------------------------------------+
//| Persistence and audit                                             |
//+------------------------------------------------------------------+
void InitRiskWatermarks()
{
   if(!GlobalVariableCheck(GvName("initial_equity")))
      SetGlobalDouble("initial_equity", AccountEquity());
   if(!GlobalVariableCheck(GvName("equity_high_water")))
      SetGlobalDouble("equity_high_water", AccountEquity());
}

void RefreshRiskWatermarks()
{
   double high = GetGlobalDouble("equity_high_water", AccountEquity());
   if(AccountEquity() > high)
      SetGlobalDouble("equity_high_water", AccountEquity());
   datetime today = DayStart(TimeCurrent());
   if(today != g_dayStart)
   {
      g_dayStart = today;
      g_dayStartEquity = AccountEquity();
   }
}

string GvName(const string key)
{
   return("PGMT4_" + IntegerToString(AccountNumber()) + "_" + IntegerToString(InpMagicNumber) + "_" + g_tradeSymbol + "_" + key);
}

double GetGlobalDouble(const string key, const double fallback)
{
   string name = GvName(key);
   if(!GlobalVariableCheck(name))
      return(fallback);
   return(GlobalVariableGet(name));
}

void SetGlobalDouble(const string key, const double value)
{
   GlobalVariableSet(GvName(key), value);
}

void LoadPersistentState()
{
   int handle = FileOpen(InpStateFile, FILE_READ | FILE_TXT | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE, 0);
   if(handle == INVALID_HANDLE)
      return;
   if(!FileIsEnding(handle))
      g_lastAcceptedPacketId = FileReadString(handle);
   if(!FileIsEnding(handle))
      g_lastAcceptedFrame = (int)StrToInteger(FileReadString(handle));
   if(!FileIsEnding(handle))
      g_lastAcceptedCapture = (int)StrToInteger(FileReadString(handle));
   if(!FileIsEnding(handle))
      g_lastAcceptedState = (int)StrToInteger(FileReadString(handle));
   FileClose(handle);
}

void SavePersistentState()
{
   int handle = FileOpen(InpStateFile, FILE_WRITE | FILE_TXT | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE, 0);
   if(handle == INVALID_HANDLE)
      return;
   FileWrite(handle, g_lastAcceptedPacketId);
   FileWrite(handle, IntegerToString(g_lastAcceptedFrame));
   FileWrite(handle, IntegerToString(g_lastAcceptedCapture));
   FileWrite(handle, IntegerToString(g_lastAcceptedState));
   FileFlush(handle);
   FileClose(handle);
}

void Audit(const string action, const string packet_id, const string side, const double lots, const string detail)
{
   if(!InpWriteAuditLog)
      return;
   int handle = FileOpen(InpAuditFile, FILE_READ | FILE_WRITE | FILE_CSV | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE, 44);
   if(handle == INVALID_HANDLE)
      return;
   FileSeek(handle, 0, SEEK_END);
   FileWrite(handle,
             TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
             action,
             packet_id,
             side,
             DoubleToString(lots, 2),
             g_tradeSymbol,
             detail);
   FileFlush(handle);
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| JSON helpers: minimal strict extractor for trusted packet schema  |
//+------------------------------------------------------------------+
bool JsonGetString(const string json, const string key, string &out)
{
   out = "";
   int pos = JsonFindValueStart(json, key);
   if(pos < 0)
      return(false);
   pos = SkipWs(json, pos);
   if(pos >= StringLen(json) || StringGetChar(json, pos) != 34)
      return(false);

   string value = "";
   bool esc = false;
   for(int i = pos + 1; i < StringLen(json); i++)
   {
      int c = StringGetChar(json, i);
      if(esc)
      {
         value += DecodeEscapeChar(c);
         esc = false;
         continue;
      }
      if(c == 92)
      {
         esc = true;
         continue;
      }
      if(c == 34)
      {
         out = value;
         return(true);
      }
      value += StringSubstr(json, i, 1);
   }
   return(false);
}

bool JsonGetBool(const string json, const string key, bool &out)
{
   int pos = JsonFindValueStart(json, key);
   if(pos < 0)
      return(false);
   pos = SkipWs(json, pos);
   string t4 = StringSubstr(json, pos, 4);
   string t5 = StringSubstr(json, pos, 5);
   if(t4 == "true")
   {
      out = true;
      return(true);
   }
   if(t5 == "false")
   {
      out = false;
      return(true);
   }
   return(false);
}

bool JsonBoolEquals(const string json, const string key, const bool expected)
{
   bool value = false;
   if(!JsonGetBool(json, key, value))
      return(false);
   return(value == expected);
}

bool JsonGetInt(const string json, const string key, int &out)
{
   double value = 0.0;
   if(!JsonGetDouble(json, key, value))
      return(false);
   out = (int)MathFloor(value + 0.0000001);
   return(true);
}

bool JsonGetDouble(const string json, const string key, double &out)
{
   out = 0.0;
   int pos = JsonFindValueStart(json, key);
   if(pos < 0)
      return(false);
   pos = SkipWs(json, pos);
   string token = "";
   for(int i = pos; i < StringLen(json); i++)
   {
      int c = StringGetChar(json, i);
      if((c >= 48 && c <= 57) || c == 45 || c == 43 || c == 46 || c == 101 || c == 69)
         token += StringSubstr(json, i, 1);
      else
         break;
   }
   if(StringLen(token) <= 0)
      return(false);
   out = StrToDouble(token);
   return(true);
}

string JsonGetObject(const string json, const string key)
{
   int pos = JsonFindValueStart(json, key);
   if(pos < 0)
      return("");
   pos = SkipWs(json, pos);
   if(pos >= StringLen(json) || StringGetChar(json, pos) != 123)
      return("");
   return(ExtractObjectAt(json, pos));
}

int JsonFindValueStart(const string json, const string key)
{
   string pattern = "\"" + key + "\"";
   int pos = 0;
   while(true)
   {
      pos = StringFind(json, pattern, pos);
      if(pos < 0)
         return(-1);
      int after = pos + StringLen(pattern);
      int colon = SkipWs(json, after);
      if(colon < StringLen(json) && StringGetChar(json, colon) == 58)
         return(colon + 1);
      pos = after;
   }
   return(-1);
}

string ExtractObjectAt(const string json, const int start)
{
   int depth = 0;
   bool in_string = false;
   bool esc = false;
   for(int i = start; i < StringLen(json); i++)
   {
      int c = StringGetChar(json, i);
      if(in_string)
      {
         if(esc)
         {
            esc = false;
            continue;
         }
         if(c == 92)
         {
            esc = true;
            continue;
         }
         if(c == 34)
            in_string = false;
         continue;
      }
      if(c == 34)
      {
         in_string = true;
         continue;
      }
      if(c == 123)
         depth++;
      else if(c == 125)
      {
         depth--;
         if(depth == 0)
            return(StringSubstr(json, start, i - start + 1));
      }
   }
   return("");
}

int SkipWs(const string text, int pos)
{
   while(pos < StringLen(text))
   {
      int c = StringGetChar(text, pos);
      if(c != 32 && c != 9 && c != 13 && c != 10)
         break;
      pos++;
   }
   return(pos);
}

string DecodeEscapeChar(const int c)
{
   if(c == 110)
      return("\n");
   if(c == 114)
      return("\r");
   if(c == 116)
      return("\t");
   return(CharToString((uchar)c));
}

//+------------------------------------------------------------------+
//| Utility                                                           |
//+------------------------------------------------------------------+
string Trim(const string value)
{
   int left = 0;
   int right = StringLen(value) - 1;
   while(left <= right)
   {
      int c_left = StringGetChar(value, left);
      if(c_left != 32 && c_left != 9 && c_left != 13 && c_left != 10)
         break;
      left++;
   }
   while(right >= left)
   {
      int c_right = StringGetChar(value, right);
      if(c_right != 32 && c_right != 9 && c_right != 13 && c_right != 10)
         break;
      right--;
   }
   if(right < left)
      return("");
   return(StringSubstr(value, left, right - left + 1));
}

string Upper(const string value)
{
   string out = "";
   for(int i = 0; i < StringLen(value); i++)
   {
      int c = StringGetChar(value, i);
      if(c >= 97 && c <= 122)
         out += CharToString((uchar)(c - 32));
      else
         out += StringSubstr(value, i, 1);
   }
   return(out);
}

string BoolText(const bool value)
{
   return(value ? "true" : "false");
}

datetime DayStart(const datetime value)
{
   return(StrToTime(TimeToString(value, TIME_DATE)));
}

string UrlEncodeLite(const string value)
{
   string out = "";
   for(int i = 0; i < StringLen(value); i++)
   {
      string ch = StringSubstr(value, i, 1);
      int c = StringGetChar(value, i);
      if((c >= 65 && c <= 90) || (c >= 97 && c <= 122) || (c >= 48 && c <= 57) || ch == "-" || ch == "_" || ch == ".")
         out += ch;
      else if(ch == " ")
         out += "%20";
      else
         out += ch;
   }
   return(out);
}

string NormalizeAllowancePackageType(const string value)
{
   string normalized = Upper(Trim(value));
   if(normalized == "INTRADAY" || normalized == "ENTER_NOW" || normalized == "INTRADAY_ENTER_NOW" || normalized == "PGI")
      return("INTRADAY_ENTER_NOW");
   if(normalized == "SWING" || normalized == "SWING_DISCIPLINED" || normalized == "PGS")
      return("SWING");
   if(StringFind(normalized, "PGI ", 0) == 0)
      return("INTRADAY_ENTER_NOW");
   if(StringFind(normalized, "PGS ", 0) == 0)
      return("SWING");
   return(normalized);
}

bool IsKnownAllowancePackage(const string package_type)
{
   string normalized = NormalizeAllowancePackageType(package_type);
   return(normalized == "INTRADAY_ENTER_NOW" || normalized == "SWING");
}

string PackageCommentCode(const string package_type)
{
   string normalized = NormalizeAllowancePackageType(package_type);
   if(normalized == "INTRADAY_ENTER_NOW")
      return("PGI");
   if(normalized == "SWING")
      return("PGS");
   return("PGV3");
}

string OrderAllowancePackageType()
{
   return(NormalizeAllowancePackageType(OrderComment()));
}

string BuildOrderComment(const string packet_id, const string package_type)
{
   string comment = PackageCommentCode(package_type) + " " + packet_id;
   if(StringLen(comment) > 31)
      comment = StringSubstr(comment, 0, 31);
   return(comment);
}

void SetStatus(const string status)
{
   if(status == g_lastStatus)
      return;
   g_lastStatus = status;
   Print("PhoenixGuard: ", status);
}

string TradeErrorText(const int err)
{
   if(err == 1)   return("ERR_NO_RESULT");
   if(err == 2)   return("ERR_COMMON_ERROR");
   if(err == 3)   return("ERR_INVALID_TRADE_PARAMETERS");
   if(err == 4)   return("ERR_SERVER_BUSY");
   if(err == 5)   return("ERR_OLD_VERSION");
   if(err == 6)   return("ERR_NO_CONNECTION");
   if(err == 8)   return("ERR_TOO_FREQUENT_REQUESTS");
   if(err == 64)  return("ERR_ACCOUNT_DISABLED");
   if(err == 65)  return("ERR_INVALID_ACCOUNT");
   if(err == 129) return("ERR_INVALID_PRICE");
   if(err == 130) return("ERR_INVALID_STOPS");
   if(err == 131) return("ERR_INVALID_TRADE_VOLUME");
   if(err == 132) return("ERR_MARKET_CLOSED");
   if(err == 133) return("ERR_TRADE_DISABLED");
   if(err == 134) return("ERR_NOT_ENOUGH_MONEY");
   if(err == 135) return("ERR_PRICE_CHANGED");
   if(err == 136) return("ERR_OFF_QUOTES");
   if(err == 138) return("ERR_REQUOTE");
   if(err == 146) return("ERR_TRADE_CONTEXT_BUSY");
   if(err == 147) return("ERR_TRADE_EXPIRATION_DENIED");
   if(err == 148) return("ERR_TRADE_TOO_MANY_ORDERS");
   return("ERR_" + IntegerToString(err));
}
