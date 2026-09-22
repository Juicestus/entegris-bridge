#pragma once

#include "Util.h"
#include "Config.h"

#define NCS_MAX_PKT		4096

enum class NCSCommand : uint16_t			// TODO: rename
{
	SEND_PACKET = 0,		// tunnels a pump frame, 1b
	GET_INTERFACES = 1,
	GET_NAME = 2,
	GET_VERSION = 3,
	QUERY_INTERFACE = 4,
	LOCK_INTERFACE = 5,		// never sent
	UNLOCK_INTERFACE = 6,	// never sent
};

enum class NCSReturnCode : uint16_t			// TODO: rename
{
	NONE = 0,
	INTERNAL = 1001,
	UNKNOWN_COMMAND = 1002,
	MALFORMED_PACKET = 1003,
	UNKNOWN_SERIAL_PORT = 1004,			// also what a wrong logical_com1 gives us
	DISABLED_SERIAL_PORT = 1005,
	INPUT_INVALID_CRC = 1006,			// our frame's CRC, checked before it hits the bus
	EXTRA_DATA = 1007,
	INVALID_SERIAL_DATA = 1008,
	LOCKED_INTERFACE = 1010,			// no 1009 in the spec
	NOT_LOCKED_INTERFACE = 1011,
	INVALID_LOCK_CODE = 1012,
	SERIAL_COMMAND_FAILED = 1013,
	SERIAL_SHORT_READ = 1014,
	SERIAL_READ_TIMEOUT = 1015,
	SERIAL_OUTPUT_INVALID_CRC = 1016,	// pump's reply CRC
};


#pragma pack(push, 1)
struct NCSReqHeader
{
	NCSCommand	cmd;			// switch to underlying type? since byteorder not garunteed
	uint16_t	size;			// see cfg->size_includes_header
	uint16_t	timeout;		// TODO: units? 
	uint16_t	ctrl;			// TODO: look into CTRL_* bits
	uint16_t	lserial;		 
	uint32_t	lock;		
	uint32_t	reserved;	
};

struct NCSRespHeader
{
	NCSReturnCode	rc;				// switch to underlying type? since byteorder not garunteed
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
	NCSCommand	cmd{ NCSCommand::SEND_PACKET };
	ushort		lserial{ 0 };
	ushort		ctrl{ 0 };
	byte*		data{ nullptr };
	ushort		data_len{ 0 };

	ssize_t Build(byte* buf, size_t cap, const Config* cfg);
};

struct NCSResponse
{
	byte			buf[NCS_MAX_PKT];
	size_t			len{ 0 };
	NCSReturnCode	rc{ NCSReturnCode::NONE };
	byte*			body{ nullptr };
	size_t			body_len{ 0 };

	ssize_t Parse(const Config* cfg);
};
