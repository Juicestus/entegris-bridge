#include "NCSProtocol.h"

ssize_t NCSRequest::Build(byte* buf, size_t cap, const Config* cfg)
{

	if (cmd == NCSCommand::LOCK_INTERFACE || cmd == NCSCommand::UNLOCK_INTERFACE)
	{
		return -1;		// Refuse these commands
	}
	if (data_len && !data)
	{
		return -1;		// No data for packet that should have data
	}
	
	size_t pkt_len = sizeof(NCSReqHeader) + data_len;
	if (pkt_len > cap || pkt_len > UINT16_MAX)
	{
		return -1;		// Packet too large
	}
	
	NCSReqHeader* hdr = (NCSReqHeader*)buf;
	memset(hdr, 0, sizeof(NCSReqHeader));

	hdr->cmd = (NCSCommand)cfg->Net16((ushort)cmd);
	hdr->size = cfg->Net16(cfg->size_includes_header ? pkt_len : data_len);
	hdr->timeout = cfg->Net16(cfg->timeout_ms);
	hdr->ctrl = cfg->Net16(ctrl);
	hdr->lserial = cfg->Net16(lserial);

	if (data_len) 
		memcpy(buf + NCS_HDR_LEN, data, data_len);

	return pkt_len;
}


ssize_t NCSResponse::Parse(const Config* cfg)
{
	rc = NCSReturnCode::NO_ERR;
	body = nullptr;
	body_len = 0;

	if (len < sizeof(NCSRespHeader) || len > NCS_MAX_PKT)
	{
		return -1;	// Packet to big or too small
	}

	NCSRespHeader* hdr = (NCSRespHeader*)buf;
	rc = (NCSReturnCode)cfg->Host16((ushort)hdr->rc);
	uint16_t size = cfg->Host16(hdr->size);

	body = buf + sizeof(NCSRespHeader);
	body_len = len - sizeof(NCSRespHeader);

	if ((cfg->size_includes_header ? len : body_len) != size)
	{
		return -1;	// Incorrect size
	}
	
	return 0;
}


ssize_t NCSResponse::CopyStrOut(char* buf, size_t cap)
{
	if (!buf || !cap)
	{
		return -1;			// no output space
	}
	if (!body_len)
	{
		return -1;			// no string
	}
	ssize_t n = std::min(body_len, cap - 1);
	memcpy(buf, body, n);
	buf[n] = 0;
	return n;
}

