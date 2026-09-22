#pragma once

#include "Util.h"
#include "Config.h"

#define MAX_NCS_PKT		4096

enum class NCSCommand : uint16_t
{
	NCS_SEND_PACKET = 0,		// tunnels a pump frame, 1b
	NCS_GET_INTERFACES = 1,
	NCS_GET_NAME = 2,
	NCS_GET_VERSION = 3,
	NCS_QUERY_INTERFACE = 4,
	NCS_LOCK_INTERFACE = 5,		// never sent
	NCS_UNLOCK_INTERFACE = 6,	// never sent
};

enum class NCSReturnCode : uint16_t
{
	ERR_NO_ERROR = 0,
	ERR_INTERNAL = 1001,
	ERR_UNKNOWN_COMMAND = 1002,
	ERR_MALFORMED_PACKET = 1003,
	ERR_UNKNOWN_SERIAL_PORT = 1004,			// also what a wrong logical_com1 gives us
	ERR_DISABLED_SERIAL_PORT = 1005,
	ERR_INPUT_INVALID_CRC = 1006,			// our frame's CRC, checked before it hits the bus
	ERR_EXTRA_DATA = 1007,
	ERR_INVALID_SERIAL_DATA = 1008,
	ERR_LOCKED_INTERFACE = 1010,			// no 1009 in the spec
	ERR_NOT_LOCKED_INTERFACE = 1011,
	ERR_INVALID_LOCK_CODE = 1012,
	ERR_SERIAL_COMMAND_FAILED = 1013,
	ERR_SERIAL_SHORT_READ = 1014,
	ERR_SERIAL_READ_TIMEOUT = 1015,
	ERR_SERIAL_OUTPUT_INVALID_CRC = 1016,	// pump's reply CRC
};


#pragma pack(push, 1)
struct NCSReqHeader
{
	NCSCommand	cmd;
	uint16_t	size;			// see cfg->size_includes_header
	uint16_t	timeout;		// TODO: units? 
	uint16_t	ctrl;			// TODO: look into CTRL_* bits
	uint16_t	lserial;		 
	uint32_t	lock;		
	uint32_t	reserved;	
};

struct NCSRespHeader
{
	NCSReturnCode	rc;
	uint16_t		size;
	uint16_t		reserved[3];
	uint32_t		reserved2[2];
};
#pragma pack(pop)

/* Enforce header length */
#define NCS_HDR_LEN		18

static_assert(sizeof(NCSReqHeader) == NCS_HDR_LEN, "");
static_assert(sizeof(NCSRespHeader) == NCS_HDR_LEN, "");


struct NCSRequest
{
	NCSCommand	cmd{ NCSCommand::NCS_SEND_PACKET };
	ushort		lserial{ 0 };
	ushort		ctrl{ 0 };
	byte*		data{ nullptr };
	ushort		data_len{ 0 };

	int Build(byte* buf, size_t cap, const Config* cfg);
};

struct NCSResponse
{
	byte			buf[MAX_NCS_PKT];
	size_t			len;
	NCSReturnCode	rc;
	byte*			body;
	size_t			body_len;

	int Parse(const Config* cfg);
};
